"""Frame builders: `DomainEvent` stream -> typed polars frames.

These are PURE FUNCTIONS of the event stream (CLAUDE.md's Phase 2
invariant): no I/O, no clock, no network, no database. Loading is somebody
else's job — `f1_kernel.sources` hands you events, these turn them into
frames. Same events in, byte-identical frames out, which is what makes
every analytic built on them backtestable.

One code path serves live-derived and historical-derived data because both
arrive as the same `DomainEvent` union. Where history is thinner (no
track-status stream, coarser timestamps) the derivation degrades
explicitly — the `*_samples` / `_flags` columns say what was actually
observed rather than papering over the difference.

The derivations that are worth knowing about, all of them documented per
column in `schemas.LAP_FRAME_DOCS`:

- **The lap window** `(lap_start_utc, lap_end_utc]` is the spine. Gaps and
  track status are summarized over it, and it is reconstructed as
  `end - lap_time`, falling back to the driver's previous lap end when the
  feed sent no lap time.
- **Gaps are summarized, not sampled.** `gap_ahead_s` is the MEDIAN of the
  interval samples inside the window, so one momentary lunge does not make
  a lap look like it was run in traffic — and `gap_ahead_samples` reports
  how much evidence there was.
- **Telemetry distance is integrated from speed**, because F1's telemetry
  has no distance channel.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from f1_domain import (
    CarTelemetry,
    Compound,
    DomainEvent,
    GapUpdated,
    LapCompleted,
    LapCounterUpdated,
    PitEntry,
    PitExit,
    PositionChanged,
    Provenance,
    SessionInfoUpdated,
    SessionKey,
    StintStarted,
)
from f1_kernel.circuit import Circuit, circuit_for_session
from f1_kernel.schemas import (
    DRS_OPEN_CODES,
    LAP_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    TELEMETRY_FRAME_SCHEMA,
    empty_frame,
)
from f1_kernel.status import StatusInterval, status_during, statuses_during, track_status_timeline

MAX_INTEGRATION_GAP_S = 2.0
"""Telemetry samples further apart than this contribute no distance. A hole
in the stream (pit stop, dropout) would otherwise integrate the last known
speed across the whole gap and invent hundreds of metres."""


@dataclass(frozen=True, slots=True)
class SessionFrames:
    """Everything an analytic needs for one session: the frames, the
    session's identity, and its circuit metadata when we ship that circuit.

    This is what the backtest harness hands to an analytic, and what an
    analytic signature should take — passing the frames separately makes it
    too easy to run an analytic against another session's circuit.
    """

    session_key: SessionKey
    laps: pl.DataFrame
    stints: pl.DataFrame
    circuit: Circuit | None = None
    event_name: str | None = None

    @property
    def drivers(self) -> list[str]:
        """Every driver with at least one lap, sorted."""
        if self.laps.is_empty():
            return []
        return sorted(str(d) for d in self.laps["driver"].unique() if d is not None)


# ---------------------------------------------------------------------------
# lap frame
# ---------------------------------------------------------------------------


def lap_frame_from_events(events: Iterable[DomainEvent], session_key: SessionKey) -> pl.DataFrame:
    """One row per completed lap. See `schemas.LAP_FRAME_SCHEMA`."""
    materialized = [e for e in events if e.session_key == session_key]
    laps = _collect_laps(materialized)
    if not laps:
        return empty_frame(LAP_FRAME_SCHEMA)

    stints = _collect_stints(materialized)
    pit_in, pit_out = _collect_pits(materialized)
    gaps = _GapIndex(materialized)
    positions = _PositionIndex(materialized)
    timeline = track_status_timeline(materialized)

    columns: dict[str, list[object]] = {name: [] for name in LAP_FRAME_SCHEMA}
    previous_end: dict[str, datetime] = {}

    for (driver, lap_number), lap in sorted(laps.items()):
        end = lap.ts_utc
        start = _lap_start(lap, previous_end.get(driver))
        previous_end[driver] = end
        stint = _stint_for_lap(stints.get(driver, ()), lap_number)
        window_gaps = gaps.window(driver, start, end)

        columns["session_year"].append(session_key.year)
        columns["session_round"].append(session_key.round)
        columns["session_type"].append(session_key.session_type.value)
        columns["driver"].append(driver)
        columns["lap_number"].append(lap_number)
        columns["lap_start_utc"].append(start)
        columns["lap_end_utc"].append(end)
        columns["lap_time_s"].append(_seconds(lap.lap_time))
        sectors = lap.sector_times or []
        for index in range(3):
            value = sectors[index] if index < len(sectors) else None
            columns[f"sector{index + 1}_s"].append(_seconds(value))
        columns["is_personal_best"].append(lap.is_personal_best)
        columns["compound"].append(_compound_value(lap.compound, stint))
        columns["tyre_age"].append(_tyre_age(lap, stint, lap_number))
        columns["stint_number"].append(stint.stint_number if stint is not None else None)
        columns["position"].append(positions.at(driver, end))
        columns["gap_ahead_s"].append(_median(window_gaps.ahead))
        columns["gap_ahead_min_s"].append(min(window_gaps.ahead, default=None))
        columns["gap_ahead_samples"].append(len(window_gaps.ahead))
        columns["gap_to_leader_s"].append(_median(window_gaps.leader))
        columns["track_status"].append(status_during(timeline, start, end).value)
        columns["track_status_flags"].append(
            [status.value for status in statuses_during(timeline, start, end)]
        )
        columns["is_pit_in"].append((driver, lap_number) in pit_in)
        columns["is_pit_out"].append((driver, lap_number) in pit_out)
        columns["provenance"].append(lap.provenance.value)

    return pl.DataFrame(columns, schema=LAP_FRAME_SCHEMA)


def _lap_start(lap: LapCompleted, previous_end: datetime | None) -> datetime:
    """The lap window's start: `end - lap_time` when we have a lap time,
    else the driver's previous lap end, else the end itself (a zero-width
    window — honest about knowing nothing, and it simply matches no gap
    samples rather than inventing a window of arbitrary length)."""
    if lap.lap_time is not None:
        return lap.ts_utc - lap.lap_time
    if previous_end is not None:
        return previous_end
    return lap.ts_utc


def _collect_laps(events: Sequence[DomainEvent]) -> dict[tuple[str, int], LapCompleted]:
    """The first `LapCompleted` per (driver, lap). The feed re-sends
    constantly; the first observation is the one the state machine emitted
    on the genuine transition, and taking it keeps the frame independent of
    how many times a lap was re-announced."""
    laps: dict[tuple[str, int], LapCompleted] = {}
    for event in events:
        if not isinstance(event, LapCompleted):
            continue
        if event.driver is None or event.lap_number is None:
            continue
        laps.setdefault((event.driver, event.lap_number), event)
    return laps


def _collect_stints(events: Sequence[DomainEvent]) -> dict[str, tuple[StintStarted, ...]]:
    """Stints per driver, ordered by start lap."""
    by_driver: dict[str, dict[int, StintStarted]] = {}
    for event in events:
        if not isinstance(event, StintStarted):
            continue
        if event.driver is None or event.stint_number is None:
            continue
        by_driver.setdefault(event.driver, {}).setdefault(event.stint_number, event)
    return {
        driver: tuple(
            sorted(stints.values(), key=lambda s: (s.start_lap or 0, s.stint_number or 0))
        )
        for driver, stints in by_driver.items()
    }


def _stint_for_lap(stints: Sequence[StintStarted], lap_number: int) -> StintStarted | None:
    """The stint a lap belongs to: the last one that started at or before
    it. A lap before any recorded stint start belongs to none."""
    found: StintStarted | None = None
    for stint in stints:
        if stint.start_lap is not None and stint.start_lap <= lap_number:
            found = stint
        else:
            break
    return found


def _collect_pits(
    events: Sequence[DomainEvent],
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    entries: set[tuple[str, int]] = set()
    exits: set[tuple[str, int]] = set()
    for event in events:
        if not isinstance(event, PitEntry | PitExit):
            continue
        if event.driver is None or event.lap_number is None:
            continue
        target = entries if isinstance(event, PitEntry) else exits
        target.add((event.driver, event.lap_number))
    return entries, exits


@dataclass(frozen=True, slots=True)
class _WindowGaps:
    ahead: list[float]
    leader: list[float]


class _GapIndex:
    """Per-driver gap samples, sorted by time, queried by lap window.

    A race carries tens of thousands of interval updates, so the samples are
    bisected rather than rescanned per lap.
    """

    def __init__(self, events: Sequence[DomainEvent]) -> None:
        collected: dict[str, list[tuple[datetime, float | None, float | None]]] = {}
        for event in events:
            if not isinstance(event, GapUpdated) or event.driver is None:
                continue
            collected.setdefault(event.driver, []).append(
                (
                    event.ts_utc,
                    _seconds(event.interval_to_ahead),
                    _seconds(event.gap_to_leader),
                )
            )
        self._times: dict[str, list[datetime]] = {}
        self._samples: dict[str, list[tuple[float | None, float | None]]] = {}
        for driver, rows in collected.items():
            rows.sort(key=lambda row: row[0])
            self._times[driver] = [row[0] for row in rows]
            self._samples[driver] = [(row[1], row[2]) for row in rows]

    def window(self, driver: str, start: datetime, end: datetime) -> _WindowGaps:
        """Samples in `(start, end]`. Lapped drivers send no duration gap at
        all (`laps_down` instead), so nulls are skipped rather than zeroed."""
        times = self._times.get(driver)
        if not times:
            return _WindowGaps([], [])
        low = bisect.bisect_right(times, start)
        high = bisect.bisect_right(times, end)
        ahead: list[float] = []
        leader: list[float] = []
        for interval, to_leader in self._samples[driver][low:high]:
            if interval is not None:
                ahead.append(interval)
            if to_leader is not None:
                leader.append(to_leader)
        return _WindowGaps(ahead, leader)


class _PositionIndex:
    """Per-driver position changes, queried as "position as of time T"."""

    def __init__(self, events: Sequence[DomainEvent]) -> None:
        collected: dict[str, list[tuple[datetime, int]]] = {}
        for event in events:
            if not isinstance(event, PositionChanged):
                continue
            if event.driver is None or event.position is None:
                continue
            collected.setdefault(event.driver, []).append((event.ts_utc, event.position))
        self._times: dict[str, list[datetime]] = {}
        self._values: dict[str, list[int]] = {}
        for driver, rows in collected.items():
            rows.sort(key=lambda row: row[0])
            self._times[driver] = [row[0] for row in rows]
            self._values[driver] = [row[1] for row in rows]

    def at(self, driver: str, when: datetime) -> int | None:
        times = self._times.get(driver)
        if not times:
            return None
        index = bisect.bisect_right(times, when)
        return self._values[driver][index - 1] if index else None


# ---------------------------------------------------------------------------
# stint frame
# ---------------------------------------------------------------------------


def stint_frame_from_events(
    events: Iterable[DomainEvent], session_key: SessionKey, *, laps: pl.DataFrame | None = None
) -> pl.DataFrame:
    """One row per (driver, stint). See `schemas.STINT_FRAME_SCHEMA`.

    `laps` (any frame with `driver`/`lap_number`) supplies the observed lap
    counts and the final stint's end lap; pass the already-built lap frame
    to avoid rebuilding it.
    """
    materialized = [e for e in events if e.session_key == session_key]
    stints = _collect_stints(materialized)
    if not stints:
        return empty_frame(STINT_FRAME_SCHEMA)

    lap_frame_ = laps if laps is not None else lap_frame_from_events(materialized, session_key)
    laps_by_driver = _laps_by_driver(lap_frame_)

    columns: dict[str, list[object]] = {name: [] for name in STINT_FRAME_SCHEMA}
    for driver in sorted(stints):
        driver_laps = laps_by_driver.get(driver, [])
        ordered = stints[driver]
        for index, stint in enumerate(ordered):
            start_lap = stint.start_lap
            next_start = _next_start_lap(ordered, index)
            end_lap = _stint_end_lap(start_lap, next_start, driver_laps)
            in_stint = [
                lap
                for lap in driver_laps
                if (start_lap is None or lap >= start_lap) and (end_lap is None or lap <= end_lap)
            ]
            columns["session_year"].append(session_key.year)
            columns["session_round"].append(session_key.round)
            columns["session_type"].append(session_key.session_type.value)
            columns["driver"].append(driver)
            columns["stint_number"].append(stint.stint_number)
            columns["compound"].append(stint.compound.value if stint.compound is not None else None)
            columns["start_lap"].append(start_lap)
            columns["end_lap"].append(end_lap)
            columns["lap_count"].append(len(in_stint))
            columns["tyre_age_at_start"].append(stint.tyre_age_at_start)
            columns["provenance"].append(stint.provenance.value)

    return pl.DataFrame(columns, schema=STINT_FRAME_SCHEMA).sort("driver", "stint_number")


def _next_start_lap(stints: Sequence[StintStarted], index: int) -> int | None:
    for later in stints[index + 1 :]:
        if later.start_lap is not None:
            return later.start_lap
    return None


def _stint_end_lap(
    start_lap: int | None, next_start: int | None, driver_laps: Sequence[int]
) -> int | None:
    if next_start is not None:
        return next_start - 1
    if driver_laps:
        return max(driver_laps)
    return start_lap


def _laps_by_driver(laps: pl.DataFrame) -> dict[str, list[int]]:
    if laps.is_empty():
        return {}
    result: dict[str, list[int]] = {}
    for driver, lap_number in zip(laps["driver"], laps["lap_number"], strict=True):
        if driver is None or lap_number is None:
            continue
        result.setdefault(str(driver), []).append(int(lap_number))
    return {driver: sorted(values) for driver, values in result.items()}


# ---------------------------------------------------------------------------
# telemetry frame
# ---------------------------------------------------------------------------


def telemetry_frame_from_events(
    events: Iterable[DomainEvent],
    session_key: SessionKey,
    driver: str,
    *,
    laps: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per telemetry sample for `driver`, with distance integrated
    from speed and each sample tagged with its lap.

    See `schemas.TELEMETRY_FRAME_SCHEMA`. Historical sessions only carry
    telemetry when the source was asked for it (it is large and resampled —
    DATA_CONTRACTS.md); a session without it yields an empty frame rather
    than raising, so a backtest over mixed sessions keeps going.
    """
    materialized = list(events)
    samples = sorted(
        (
            event
            for event in materialized
            if isinstance(event, CarTelemetry)
            and event.session_key == session_key
            and event.driver == driver
        ),
        key=lambda event: (event.ts_utc, event.source_seq),
    )
    if not samples:
        return empty_frame(TELEMETRY_FRAME_SCHEMA)

    lap_windows = _lap_windows_for_driver(materialized, session_key, driver, laps)

    columns: dict[str, list[object]] = {name: [] for name in TELEMETRY_FRAME_SCHEMA}
    first_ts = samples[0].ts_utc
    distance = 0.0
    previous_ts: datetime | None = None
    previous_speed: float | None = None
    lap_base_distance: dict[int, float] = {}

    for sample in samples:
        if previous_ts is not None:
            gap_s = (sample.ts_utc - previous_ts).total_seconds()
            if 0 < gap_s <= MAX_INTEGRATION_GAP_S:
                mean_kph = ((previous_speed or 0.0) + (sample.speed or 0.0)) / 2.0
                distance += mean_kph / 3.6 * gap_s
        previous_ts = sample.ts_utc
        previous_speed = sample.speed

        lap_number = _lap_at(lap_windows, sample.ts_utc)
        lap_distance: float | None = None
        if lap_number is not None:
            base = lap_base_distance.setdefault(lap_number, distance)
            lap_distance = distance - base

        columns["session_year"].append(session_key.year)
        columns["session_round"].append(session_key.round)
        columns["session_type"].append(session_key.session_type.value)
        columns["driver"].append(driver)
        columns["ts_utc"].append(sample.ts_utc)
        columns["time_s"].append((sample.ts_utc - first_ts).total_seconds())
        columns["lap_number"].append(lap_number)
        columns["distance_m"].append(distance)
        columns["lap_distance_m"].append(lap_distance)
        columns["speed_kph"].append(sample.speed)
        columns["throttle_pct"].append(sample.throttle)
        columns["brake_pct"].append(sample.brake)
        columns["gear"].append(sample.gear)
        columns["rpm"].append(sample.rpm)
        columns["drs"].append(sample.drs)
        columns["drs_open"].append(sample.drs in DRS_OPEN_CODES if sample.drs is not None else None)
        columns["provenance"].append(sample.provenance.value)

    return pl.DataFrame(columns, schema=TELEMETRY_FRAME_SCHEMA)


def _lap_windows_for_driver(
    events: Sequence[DomainEvent],
    session_key: SessionKey,
    driver: str,
    laps: pl.DataFrame | None,
) -> list[tuple[datetime, datetime, int]]:
    """`(start, end, lap_number)` windows for one driver, sorted by end."""
    frame = laps if laps is not None else lap_frame_from_events(events, session_key)
    if frame.is_empty():
        return []
    driver_laps = frame.filter(pl.col("driver") == driver)
    windows = [
        (start, end, int(number))
        for start, end, number in zip(
            driver_laps["lap_start_utc"],
            driver_laps["lap_end_utc"],
            driver_laps["lap_number"],
            strict=True,
        )
        if start is not None and end is not None and number is not None
    ]
    windows.sort(key=lambda window: window[1])
    return windows


def _lap_at(windows: Sequence[tuple[datetime, datetime, int]], when: datetime) -> int | None:
    """The lap whose `(start, end]` window contains `when`, else None."""
    if not windows:
        return None
    ends = [window[1] for window in windows]
    index = bisect.bisect_left(ends, when)
    if index >= len(windows):
        return None
    start, end, lap_number = windows[index]
    return lap_number if start < when <= end else None


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def session_frames_from_events(
    events: Iterable[DomainEvent],
    session_key: SessionKey,
    *,
    event_name: str | None = None,
    venue: str | None = None,
) -> SessionFrames:
    """Build every frame for one session in a single pass over the events.

    The circuit comes from the stream's own `SessionInfoUpdated` when there
    is one (a live session: by the feed's venue name, which is unambiguous
    across seasons), else from `venue` (a backfilled session's schedule
    location), else from `event_name`."""
    materialized = list(events)
    laps = lap_frame_from_events(materialized, session_key)
    stints = stint_frame_from_events(materialized, session_key, laps=laps)
    meeting_name, stream_venue = _session_info(materialized)
    venue = stream_venue or venue
    name = event_name if event_name is not None else meeting_name
    return SessionFrames(
        session_key=session_key,
        laps=laps,
        stints=stints,
        circuit=circuit_for_session(name, venue),
        event_name=name,
    )


def race_distance_laps(events: Iterable[DomainEvent]) -> int | None:
    """How long this race is, in laps: the feed's own `LapCounterUpdated`
    total when the stream carries one, else the furthest lap anyone
    completed. None when the stream says neither.

    The fuel model's burn rate depends on it, so it must be the WHOLE race,
    never the laps seen so far: inferred mid-race from a slice, a 57-lap race
    read as 20 laps long over-removes the fuel trend and biases every
    degradation rate the undercut call is built on (`fuel_correct`'s own
    docstring). The distance is not a spoiler -- it is on screen as "38/57"
    from the first lap."""
    counter: int | None = None
    furthest: int | None = None
    for event in events:
        if isinstance(event, LapCounterUpdated) and event.total_laps is not None:
            counter = event.total_laps
        elif isinstance(event, LapCompleted) and event.lap_number is not None:
            furthest = event.lap_number if furthest is None else max(furthest, event.lap_number)
    return counter if counter is not None else furthest


def _session_info(events: Sequence[DomainEvent]) -> tuple[str | None, str | None]:
    """The latest meeting name and venue short name the stream carries."""
    meeting: str | None = None
    venue: str | None = None
    for event in events:
        if isinstance(event, SessionInfoUpdated):
            meeting = event.meeting_name or meeting
            venue = event.circuit_short_name or venue
    return meeting, venue


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None


def _median(values: Sequence[float]) -> float | None:
    """Median of the observed samples, None when there are none. Used
    rather than the mean so a single outlying sample inside a lap window
    cannot move the lap's summary."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _compound_value(compound: Compound | None, stint: StintStarted | None) -> str | None:
    """The lap's own compound, falling back to its stint's. `UNKNOWN` on the
    lap is treated as missing — the feed sends blanks mid-stint and the
    stint knows better."""
    if compound is not None and compound is not Compound.UNKNOWN:
        return compound.value
    if stint is not None and stint.compound is not None:
        return stint.compound.value
    return compound.value if compound is not None else None


def _tyre_age(lap: LapCompleted, stint: StintStarted | None, lap_number: int) -> int | None:
    """Tyre age at the end of the lap, derived from the stint when the lap
    event carries none — so age stays monotonic across a stint instead of
    holing wherever the feed dropped the field."""
    if lap.tyre_age is not None:
        return lap.tyre_age
    if stint is None or stint.start_lap is None:
        return None
    return lap_number - stint.start_lap + 1 + (stint.tyre_age_at_start or 0)


__all__ = [
    "MAX_INTEGRATION_GAP_S",
    "Provenance",
    "SessionFrames",
    "StatusInterval",
    "lap_frame_from_events",
    "session_frames_from_events",
    "stint_frame_from_events",
    "telemetry_frame_from_events",
]
