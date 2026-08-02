"""`events_from_parquet`: historical parquet -> the SAME `DomainEvent`
stream the live pipeline emits.

This is the Phase 1 seam CLAUDE.md left open in Phase 0: Phase 2's kernel
must run identically on 2023-2025 history and on live data, one code path,
two sources. Events come out in chronological order, stamped
`provenance=HISTORICAL`.

What historical CANNOT provide (documented so the kernel never assumes it):

- `source_seq` is SYNTHETIC — a dense 0..N ordinal in emission order, not a
  real wire-message seq. It orders events; it does not trace to a raw
  record (there is none).
- Timestamps are RECONSTRUCTED. FastF1's per-sample tables carry only a
  session-relative `Time`; we rebuild absolute UTC from the session start
  (`reader.session_start_utc`). Sub-second alignment across channels is
  therefore approximate, unlike the live feed's own timestamps.
- No `CarPosition` (f1_backfill exports no position table) and no
  track/session-status delta stream. `CarTelemetry` is available but
  OFF BY DEFAULT (`include_telemetry`): FastF1's telemetry is resampled and
  gap-filled (its `Source` column flags interpolated rows), so it is not
  the feed's raw per-delta channel — treat it as a lower-fidelity stand-in.
- Delta-level granularity is lost generally: history gives per-lap and
  per-event facts, not the constant re-send/partial-update stream the state
  machine folds live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pandas as pd

from f1_domain import (
    CarTelemetry,
    DomainEvent,
    DriverListUpdated,
    DriverRef,
    GapUpdated,
    LapCompleted,
    PitEntry,
    PitExit,
    PositionChanged,
    Provenance,
    RaceControlMessage,
    SectorCompleted,
    SessionKey,
    StintStarted,
    TeamRadioClip,
    WeatherSample,
)
from f1_historical_adapter.coerce import (
    compound,
    driver_number,
    opt_bool,
    opt_float,
    opt_int,
    opt_str,
    opt_timedelta,
    to_utc,
)
from f1_historical_adapter.reader import SessionTables, load_session_tables
from f1_parse import LapGap, parse_gap


class _Env(TypedDict):
    session_key: SessionKey
    ts_utc: datetime
    source_seq: int
    provenance: Provenance


EnvFactory = Callable[[datetime], _Env]


class _Collector:
    """Accumulates `(ts, event)` and yields them chronologically, assigning
    the synthetic `source_seq` in emission order. The insertion counter is
    the stable tiebreak for events sharing a timestamp, so the ordering is
    fully deterministic."""

    def __init__(self) -> None:
        self._items: list[tuple[datetime, int, DomainEvent]] = []
        self._counter = 0

    def add(self, ts: datetime | None, event: DomainEvent) -> None:
        if ts is None:
            return  # cannot place an event we can't timestamp
        self._items.append((ts, self._counter, event))
        self._counter += 1

    def latest(self) -> datetime | None:
        return max((ts for ts, _, _ in self._items), default=None)

    def ordered(self) -> Iterator[DomainEvent]:
        for seq, (_, _, event) in enumerate(
            sorted(self._items, key=lambda item: (item[0], item[1]))
        ):
            yield event.model_copy(update={"source_seq": seq})


def events_from_parquet(
    historical_root: Path,
    session_key: SessionKey,
    *,
    include_telemetry: bool = False,
) -> Iterator[DomainEvent]:
    """Yield the `DomainEvent` stream for one historical session, in
    chronological order. See the module docstring for the fidelity caveats.

    `include_telemetry` opts into the (large, resampled) `CarTelemetry`
    channel; it is off by default.
    """
    tables = load_session_tables(historical_root, session_key)
    return build_events(tables, include_telemetry=include_telemetry)


def build_events(
    tables: SessionTables, *, include_telemetry: bool = False
) -> Iterator[DomainEvent]:
    """`events_from_parquet` split from the I/O, so tests can drive it with
    in-memory `SessionTables`."""
    key = tables.session_key
    start = tables.session_start_utc
    collector = _Collector()

    def env(ts: datetime) -> _Env:
        # source_seq is a placeholder; `_Collector.ordered` overwrites it
        # with the emission ordinal.
        return {
            "session_key": key,
            "ts_utc": ts,
            "source_seq": 0,
            "provenance": Provenance.HISTORICAL,
        }

    laps = tables.get("laps")
    if laps is not None and start is not None:
        _emit_laps(laps, start, env, collector)
        _emit_stints(laps, start, env, collector)

    weather = tables.get("weather")
    if weather is not None and start is not None:
        _emit_weather(weather, start, env, collector)

    race_control = tables.get("race_control")
    if race_control is not None:
        _emit_race_control(race_control, env, collector)

    team_radio = tables.get("team_radio")
    if team_radio is not None:
        _emit_team_radio(team_radio, env, collector)

    intervals = tables.get("intervals")
    if intervals is not None:
        _emit_intervals(intervals, env, collector)

    if include_telemetry:
        telemetry = tables.get("car_telemetry")
        if telemetry is not None:
            _emit_telemetry(telemetry, env, collector)

    # Identities first (at session start), final classification last (at
    # session end), so a fold of the stream ends on the results positions.
    results = tables.get("results")
    if results is not None:
        _emit_driver_list(results, start, env, collector)
        _emit_final_positions(results, collector.latest(), env, collector)

    return collector.ordered()


# ---------------------------------------------------------------------------
# per-table builders
# ---------------------------------------------------------------------------

_SECTORS = (
    ("Sector1Time", "Sector1SessionTime"),
    ("Sector2Time", "Sector2SessionTime"),
    ("Sector3Time", "Sector3SessionTime"),
)


def _emit_laps(laps: pd.DataFrame, start: datetime, env: EnvFactory, collector: _Collector) -> None:
    ordered = laps
    if {"DriverNumber", "LapNumber"}.issubset(laps.columns):
        ordered = laps.sort_values(["DriverNumber", "LapNumber"], kind="stable")
    last_position: dict[str, int] = {}

    for rec in ordered.to_dict("records"):
        number = driver_number(rec.get("DriverNumber"))
        lap_number = opt_int(rec.get("LapNumber"))
        if number is None or lap_number is None:
            continue
        time = opt_timedelta(rec.get("Time"))
        ts = start + time if time is not None else to_utc(rec.get("LapStartDate"))
        if ts is None:
            continue

        collector.add(
            ts,
            LapCompleted(
                **env(ts),
                driver=number,
                lap_number=lap_number,
                lap_time=opt_timedelta(rec.get("LapTime")),
                sector_times=[
                    opt_timedelta(rec.get("Sector1Time")),
                    opt_timedelta(rec.get("Sector2Time")),
                    opt_timedelta(rec.get("Sector3Time")),
                ],
                is_personal_best=opt_bool(rec.get("IsPersonalBest")),
                compound=compound(rec.get("Compound")),
                tyre_age=opt_int(rec.get("TyreLife")),
            ),
        )

        for index, (dur_col, session_col) in enumerate(_SECTORS, start=1):
            duration = opt_timedelta(rec.get(dur_col))
            if duration is None:
                continue
            session_time = opt_timedelta(rec.get(session_col))
            sector_ts = start + session_time if session_time is not None else ts
            collector.add(
                sector_ts,
                SectorCompleted(
                    **env(sector_ts),
                    driver=number,
                    lap_number=lap_number,
                    sector=index,
                    time=duration,
                ),
            )

        pit_in = opt_timedelta(rec.get("PitInTime"))
        if pit_in is not None:
            pit_ts = start + pit_in
            collector.add(pit_ts, PitEntry(**env(pit_ts), driver=number, lap_number=lap_number))
        pit_out = opt_timedelta(rec.get("PitOutTime"))
        if pit_out is not None:
            pit_ts = start + pit_out
            collector.add(pit_ts, PitExit(**env(pit_ts), driver=number, lap_number=lap_number))

        position = opt_int(rec.get("Position"))
        if position is not None and last_position.get(number) != position:
            last_position[number] = position
            collector.add(ts, PositionChanged(**env(ts), driver=number, position=position))


def _emit_stints(
    laps: pd.DataFrame, start: datetime, env: EnvFactory, collector: _Collector
) -> None:
    if not {"DriverNumber", "Stint", "LapNumber"}.issubset(laps.columns):
        return
    frame = laps.dropna(subset=["DriverNumber", "Stint"])
    for _, group in frame.groupby(["DriverNumber", "Stint"], dropna=False):
        first = group.sort_values("LapNumber", kind="stable").iloc[0].to_dict()
        number = driver_number(first.get("DriverNumber"))
        stint_number = opt_int(first.get("Stint"))
        if number is None or stint_number is None:
            continue
        lap_start = opt_timedelta(first.get("LapStartTime"))
        ts = start + lap_start if lap_start is not None else to_utc(first.get("LapStartDate"))
        tyre_life = opt_int(first.get("TyreLife"))
        age_at_start = max(0, tyre_life - 1) if tyre_life is not None else None
        collector.add(
            ts,
            StintStarted(
                **env(ts if ts is not None else start),
                driver=number,
                stint_number=stint_number,
                compound=compound(first.get("Compound")),
                start_lap=opt_int(first.get("LapNumber")),
                tyre_age_at_start=age_at_start,
            ),
        )


def _emit_weather(
    weather: pd.DataFrame, start: datetime, env: EnvFactory, collector: _Collector
) -> None:
    for rec in weather.to_dict("records"):
        time = opt_timedelta(rec.get("Time"))
        if time is None:
            continue
        ts = start + time
        collector.add(
            ts,
            WeatherSample(
                **env(ts),
                air_temp=opt_float(rec.get("AirTemp")),
                track_temp=opt_float(rec.get("TrackTemp")),
                humidity=opt_float(rec.get("Humidity")),
                pressure=opt_float(rec.get("Pressure")),
                wind_speed=opt_float(rec.get("WindSpeed")),
                wind_dir=opt_float(rec.get("WindDirection")),
                rainfall=opt_bool(rec.get("Rainfall")),
            ),
        )


def _emit_race_control(rc: pd.DataFrame, env: EnvFactory, collector: _Collector) -> None:
    for rec in rc.to_dict("records"):
        ts = to_utc(rec.get("date"))
        if ts is None:
            continue
        collector.add(
            ts,
            RaceControlMessage(
                **env(ts),
                category=opt_str(rec.get("category")),
                message=opt_str(rec.get("message")),
                flag=opt_str(rec.get("flag")),
                scope=opt_str(rec.get("scope")),
                driver=driver_number(rec.get("driver_number")),
                lap=opt_int(rec.get("lap_number")),
            ),
        )


def _emit_team_radio(radio: pd.DataFrame, env: EnvFactory, collector: _Collector) -> None:
    for rec in radio.to_dict("records"):
        ts = to_utc(rec.get("date"))
        if ts is None:
            continue
        collector.add(
            ts,
            TeamRadioClip(
                **env(ts),
                driver=driver_number(rec.get("driver_number")),
                audio_url=opt_str(rec.get("recording_url")),
                captured_at=ts,
            ),
        )


def _emit_intervals(intervals: pd.DataFrame, env: EnvFactory, collector: _Collector) -> None:
    for rec in intervals.to_dict("records"):
        ts = to_utc(rec.get("date"))
        if ts is None:
            continue
        gap = parse_gap(opt_str(rec.get("gap_to_leader")))
        interval = parse_gap(opt_str(rec.get("interval")))
        collector.add(
            ts,
            GapUpdated(
                **env(ts),
                driver=driver_number(rec.get("driver_number")),
                gap_to_leader=gap if isinstance(gap, timedelta) else None,
                laps_down=gap.laps if isinstance(gap, LapGap) else None,
                interval_to_ahead=interval if isinstance(interval, timedelta) else None,
            ),
        )


def _emit_telemetry(telemetry: pd.DataFrame, env: EnvFactory, collector: _Collector) -> None:
    for rec in telemetry.to_dict("records"):
        ts = to_utc(rec.get("Date"))
        if ts is None:
            continue
        brake = opt_bool(rec.get("Brake"))
        collector.add(
            ts,
            CarTelemetry(
                **env(ts),
                driver=driver_number(rec.get("DriverNumber")),
                speed=opt_float(rec.get("Speed")),
                rpm=opt_int(rec.get("RPM")),
                gear=opt_int(rec.get("nGear")),
                throttle=opt_float(rec.get("Throttle")),
                brake=None if brake is None else (100.0 if brake else 0.0),
                drs=opt_int(rec.get("DRS")),
            ),
        )


def _emit_driver_list(
    results: pd.DataFrame, start: datetime | None, env: EnvFactory, collector: _Collector
) -> None:
    if start is None:
        return
    identities: list[DriverRef] = []
    for rec in results.to_dict("records"):
        number = driver_number(rec.get("DriverNumber"))
        if number is None:
            continue
        identities.append(
            DriverRef(
                number=number,
                code=opt_str(rec.get("Abbreviation")),
                team=opt_str(rec.get("TeamName")),
            )
        )
    if identities:
        collector.add(start, DriverListUpdated(**env(start), drivers=identities))


def _emit_final_positions(
    results: pd.DataFrame, end: datetime | None, env: EnvFactory, collector: _Collector
) -> None:
    if end is None:
        return
    for rec in results.to_dict("records"):
        number = driver_number(rec.get("DriverNumber"))
        position = opt_int(rec.get("Position"))
        if number is None or position is None:
            continue
        collector.add(end, PositionChanged(**env(end), driver=number, position=position))
