"""Pure materialization: one `DomainEvent` -> the rows it writes.

Every event writes exactly one `events` row (the append-only log, the
source of truth). Events that also have a typed relational home
(`LapCompleted` -> `laps`, `CarTelemetry` -> `car_telemetry`, ...) write a
second row into that table. Everything here is a pure function of the
event and its `event_index`: no clock, no I/O, no DB — this is what makes
the sink "a SINK, not a dependency of the transform" (CLAUDE.md) and what
lets `MemorySink` and `PostgresSink` share one code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from psycopg.types.json import Jsonb

from f1_domain import (
    CarPosition,
    CarTelemetry,
    DomainEvent,
    LapCompleted,
    PitEntry,
    PitExit,
    RaceControlMessage,
    StintStarted,
    TeamRadioClip,
    WeatherSample,
)
from f1_domain.events import EventBase


@dataclass(frozen=True, slots=True)
class TableRow:
    """A row bound for `table`. `values` is keyed by column name; the sink
    orders it against `schema.TABLE_COLUMNS[table]`."""

    table: str
    values: dict[str, object]


def _seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None


def _session_values(event: EventBase) -> dict[str, object]:
    key = event.session_key
    return {
        "session_year": key.year,
        "session_round": key.round,
        "session_type": key.session_type.value,
    }


def _base_values(event: EventBase, event_index: int) -> dict[str, object]:
    """Session identity + provenance/seq columns shared by the typed tables."""
    return {
        **_session_values(event),
        "ts_utc": event.ts_utc,
        "source_seq": event.source_seq,
        "event_index": event_index,
        "provenance": event.provenance.value,
    }


def _sector(times: list[timedelta | None] | None, index: int) -> float | None:
    if times is None or index >= len(times):
        return None
    return _seconds(times[index])


def derive_rows(event: DomainEvent, event_index: int) -> list[TableRow]:
    """The rows one event writes: always the `events` log row, plus a typed
    row when the event has a relational home. `event_index` is the event's
    position within its originating record's emission (see `schema`)."""
    rows: list[TableRow] = [
        TableRow(
            "events",
            {
                **_session_values(event),
                "ts_utc": event.ts_utc,
                "source_seq": event.source_seq,
                "event_index": event_index,
                "kind": event.kind,
                # The canonical JSON of the whole event: replay_events reads
                # this back and revalidates it, so the log round-trips
                # losslessly (the determinism contract).
                "payload": Jsonb(event.model_dump(mode="json")),
            },
        )
    ]

    typed = _typed_row(event, event_index)
    if typed is not None:
        rows.append(typed)
    return rows


def _typed_row(event: DomainEvent, event_index: int) -> TableRow | None:
    base = _base_values(event, event_index)

    if isinstance(event, LapCompleted):
        if event.driver is None or event.lap_number is None:
            return None  # a lap row must be keyed by (driver, lap_number)
        return TableRow(
            "laps",
            {
                **_session_values(event),
                "driver": event.driver,
                "lap_number": event.lap_number,
                "ts_utc": event.ts_utc,
                "source_seq": event.source_seq,
                "event_index": event_index,
                "lap_time_s": _seconds(event.lap_time),
                "sector1_s": _sector(event.sector_times, 0),
                "sector2_s": _sector(event.sector_times, 1),
                "sector3_s": _sector(event.sector_times, 2),
                "is_personal_best": event.is_personal_best,
                "compound": event.compound.value if event.compound is not None else None,
                "tyre_age": event.tyre_age,
                "provenance": event.provenance.value,
            },
        )

    if isinstance(event, CarTelemetry):
        if event.driver is None:
            return None
        return TableRow(
            "car_telemetry",
            {
                **base,
                "driver": event.driver,
                "speed": event.speed,
                "rpm": event.rpm,
                "gear": event.gear,
                "throttle": event.throttle,
                "brake": event.brake,
                "drs": event.drs,
            },
        )

    if isinstance(event, CarPosition):
        if event.driver is None:
            return None
        return TableRow(
            "car_positions",
            {
                **base,
                "driver": event.driver,
                "x": event.x,
                "y": event.y,
                "z": event.z,
                "status": event.status,
            },
        )

    if isinstance(event, StintStarted):
        if event.driver is None or event.stint_number is None:
            return None
        return TableRow(
            "stints",
            {
                **_session_values(event),
                "driver": event.driver,
                "stint_number": event.stint_number,
                "ts_utc": event.ts_utc,
                "source_seq": event.source_seq,
                "event_index": event_index,
                "compound": event.compound.value if event.compound is not None else None,
                "start_lap": event.start_lap,
                "tyre_age_at_start": event.tyre_age_at_start,
                "provenance": event.provenance.value,
            },
        )

    if isinstance(event, PitEntry | PitExit):
        if event.driver is None:
            return None
        return TableRow(
            "pit_stops",
            {
                **base,
                "driver": event.driver,
                "event": "entry" if isinstance(event, PitEntry) else "exit",
                "lap_number": event.lap_number,
            },
        )

    if isinstance(event, RaceControlMessage):
        return TableRow(
            "race_control",
            {
                **base,
                "category": event.category,
                "message": event.message,
                "flag": event.flag,
                "scope": event.scope,
                "driver": event.driver,
                "lap": event.lap,
            },
        )

    if isinstance(event, WeatherSample):
        return TableRow(
            "weather",
            {
                **base,
                "air_temp": event.air_temp,
                "track_temp": event.track_temp,
                "humidity": event.humidity,
                "pressure": event.pressure,
                "wind_speed": event.wind_speed,
                "wind_dir": event.wind_dir,
                "rainfall": event.rainfall,
            },
        )

    if isinstance(event, TeamRadioClip):
        return TableRow(
            "team_radio",
            {
                **base,
                "driver": event.driver,
                "audio_url": event.audio_url,
                "captured_at": event.captured_at,
            },
        )

    # SectorCompleted, GapUpdated, PositionChanged, DriverListUpdated,
    # SessionStatusChanged, TrackStatusChanged, UnknownEvent: logged in
    # `events` only. Their current-state lives in the materialized
    # `sessions`/`drivers` tables (written from `SessionState`).
    return None
