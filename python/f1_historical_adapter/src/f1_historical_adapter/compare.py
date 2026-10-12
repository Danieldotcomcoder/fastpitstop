"""Comparison harness: for a session with BOTH a Phase 0 recording and
historical parquet, run each path and report field-level agreement.

Discrepancies are EXPECTED — the two sources are genuinely different (live
is a raw delta stream; historical is FastF1's post-processed tables), so
this produces a REPORT, not an assertion. It exists to quantify how close
the seam is and to catch gross mismatches (wrong session, broken mapping).

The comparison itself (`compare_streams`) is a pure function of the two
event streams, so it is unit-testable without either data source.
`compare_event_streams` is the wider check run after every live session
(`f1_api.post_session`): what viewers were shown live, from the
store's event log, against F1's archive once it is published.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from f1_domain import (
    Compound,
    DomainEvent,
    LapCompleted,
    PitEntry,
    PositionChanged,
    SessionKey,
    SessionState,
    SessionType,
    StintStarted,
)

# Lap times agree if within this tolerance. Live parses the feed's
# millisecond strings; historical is FastF1's own millisecond timing — a
# few ms of drift between them is not a discrepancy worth flagging.
LAP_TIME_TOLERANCE = timedelta(milliseconds=50)

COMPETITIVE_LAP = 1.07
"""Outside a race, an archive lap the live feed has no partner for counts as
missed only within 107% of the session's fastest (F1's own yardstick for a
competitive lap): in-laps and red-flag laps are timed differently by the
two sources, and showed up as dozens of "missed" laps in the 2026
Singapore qualifying, none of them a lap anyone was trying on."""

TYRE_AGE_TOLERANCE = 1
"""Laps. The live feed counts a lap on the set a moment after the car
crosses the line, so a lap can carry the age before it; more than one lap
apart is a disagreement."""


@dataclass(frozen=True, slots=True)
class Agreement:
    """One comparable field's agreement between the two streams."""

    name: str
    matched: int
    mismatched: int
    only_live: int
    only_historical: int
    examples: tuple[str, ...] = ()

    @property
    def compared(self) -> int:
        return self.matched + self.mismatched

    @property
    def fraction(self) -> float:
        return self.matched / self.compared if self.compared else 1.0

    def render(self) -> str:
        pct = f"{self.fraction * 100:5.1f}%"
        line = (
            f"  {self.name:<16} {pct} agree "
            f"({self.matched}/{self.compared}); "
            f"only-live={self.only_live}, only-historical={self.only_historical}"
        )
        for example in self.examples:
            line += f"\n      - {example}"
        return line


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    session_key: SessionKey
    live_event_count: int
    historical_event_count: int
    agreements: tuple[Agreement, ...] = field(default_factory=tuple)

    def render(self) -> str:
        header = (
            f"Comparison for {self.session_key.year} round {self.session_key.round} "
            f"{self.session_key.session_type.value}\n"
            f"  live events: {self.live_event_count}, "
            f"historical events: {self.historical_event_count}"
        )
        return "\n".join([header, *(a.render() for a in self.agreements)])


def _lap_times(events: Iterable[DomainEvent]) -> dict[tuple[str, int], timedelta]:
    result: dict[tuple[str, int], timedelta] = {}
    for event in events:
        if (
            isinstance(event, LapCompleted)
            and event.driver is not None
            and event.lap_number is not None
            and event.lap_time is not None
        ):
            result[(event.driver, event.lap_number)] = event.lap_time
    return result


def _pit_laps(events: Iterable[DomainEvent]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for event in events:
        if (
            isinstance(event, PitEntry)
            and event.driver is not None
            and event.lap_number is not None
        ):
            result.setdefault(event.driver, []).append(event.lap_number)
    return {driver: sorted(laps) for driver, laps in result.items()}


def _final_positions(events: Iterable[DomainEvent]) -> dict[str, int]:
    """Last observed classification position per driver (folding the
    PositionChanged stream in order)."""
    result: dict[str, int] = {}
    for event in events:
        if (
            isinstance(event, PositionChanged)
            and event.driver is not None
            and event.position is not None
        ):
            result[event.driver] = event.position
    return result


def _positions_from_state(state: SessionState) -> dict[str, int]:
    return {
        number: driver.position
        for number, driver in state.drivers.items()
        if driver.position is not None
    }


def _compare_lap_times(
    live: dict[tuple[str, int], timedelta], historical: dict[tuple[str, int], timedelta]
) -> Agreement:
    common = live.keys() & historical.keys()
    matched = 0
    mismatched = 0
    examples: list[str] = []
    for key in sorted(common):
        delta = abs(live[key] - historical[key])
        if delta <= LAP_TIME_TOLERANCE:
            matched += 1
        else:
            mismatched += 1
            if len(examples) < 5:
                driver, lap = key
                examples.append(
                    f"driver {driver} lap {lap}: live {live[key]} vs historical {historical[key]}"
                )
    return Agreement(
        name="lap_times",
        matched=matched,
        mismatched=mismatched,
        only_live=len(live.keys() - historical.keys()),
        only_historical=len(historical.keys() - live.keys()),
        examples=tuple(examples),
    )


def _compare_mappings(
    name: str, live: dict[str, object], historical: dict[str, object]
) -> Agreement:
    common = live.keys() & historical.keys()
    matched = 0
    mismatched = 0
    examples: list[str] = []
    for key in sorted(common):
        if live[key] == historical[key]:
            matched += 1
        else:
            mismatched += 1
            if len(examples) < 5:
                examples.append(
                    f"driver {key}: live {live[key]!r} vs historical {historical[key]!r}"
                )
    return Agreement(
        name=name,
        matched=matched,
        mismatched=mismatched,
        only_live=len(live.keys() - historical.keys()),
        only_historical=len(historical.keys() - live.keys()),
        examples=tuple(examples),
    )


def compare_streams(
    session_key: SessionKey,
    live_events: list[DomainEvent],
    live_state: SessionState,
    historical_events: list[DomainEvent],
) -> ComparisonReport:
    """Pure comparison of an already-materialized live stream/state against a
    historical stream. Compares lap times, pit-in laps, and final positions."""
    live_laps = _lap_times(live_events)
    hist_laps = _lap_times(historical_events)
    live_pits: dict[str, object] = dict(_pit_laps(live_events))
    hist_pits: dict[str, object] = dict(_pit_laps(historical_events))
    live_pos: dict[str, object] = dict(_positions_from_state(live_state))
    hist_pos: dict[str, object] = dict(_final_positions(historical_events))

    return ComparisonReport(
        session_key=session_key,
        live_event_count=len(live_events),
        historical_event_count=len(historical_events),
        agreements=(
            _compare_lap_times(live_laps, hist_laps),
            _compare_mappings("pit_laps", live_pits, hist_pits),
            _compare_mappings("final_positions", live_pos, hist_pos),
        ),
    )


def _laps_completed(events: Iterable[DomainEvent]) -> dict[str, int]:
    """Highest lap each driver completed."""
    result: dict[str, int] = {}
    for event in events:
        if isinstance(event, LapCompleted) and event.driver and event.lap_number is not None:
            result[event.driver] = max(result.get(event.driver, 0), event.lap_number)
    return result


def _compounds(events: Iterable[DomainEvent]) -> dict[tuple[str, int], Compound]:
    """The tyre each lap was run on, where either side named one."""
    result: dict[tuple[str, int], Compound] = {}
    for event in events:
        if (
            isinstance(event, LapCompleted)
            and event.driver
            and event.lap_number is not None
            and event.compound not in (None, Compound.UNKNOWN)
        ):
            assert event.compound is not None
            result[(event.driver, event.lap_number)] = event.compound
    return result


def _tyre_ages(events: Iterable[DomainEvent]) -> dict[tuple[str, int], int]:
    result: dict[tuple[str, int], int] = {}
    for event in events:
        if (
            isinstance(event, LapCompleted)
            and event.driver
            and event.lap_number is not None
            and event.tyre_age is not None
        ):
            result[(event.driver, event.lap_number)] = event.tyre_age
    return result


def _stops(events: Iterable[DomainEvent]) -> dict[str, int]:
    """Stops per driver: the stints begun after the first lap (the timing
    tower's own count). Every driver with a stint is listed, at 0 if none."""
    result: dict[str, int] = {}
    for event in events:
        if isinstance(event, StintStarted) and event.driver:
            later = event.start_lap is not None and event.start_lap > 1
            result[event.driver] = result.get(event.driver, 0) + (1 if later else 0)
    return result


def _tyre_changes(events: Iterable[DomainEvent]) -> dict[str, int]:
    """Stops per driver in the archive: the laps after the first on which
    the compound changes or the tyre's age restarts (not the previous lap's
    plus one). Not its stint count: F1 opens a stint slot for every pit-lane
    visit, tyres changed or not, and the 2026 Singapore sprint's three
    drives through the pit lane behind the safety car, on the same
    intermediates, read as three stops for most of the field."""
    laps: dict[str, list[tuple[int, Compound | None, int | None]]] = {}
    for event in events:
        if isinstance(event, LapCompleted) and event.driver and event.lap_number is not None:
            laps.setdefault(event.driver, []).append(
                (event.lap_number, event.compound, event.tyre_age)
            )
    result: dict[str, int] = {}
    for driver, rows in laps.items():
        rows.sort(key=lambda row: row[0])
        ages = [age for _, _, age in rows]
        changes = 0
        for index in range(1, len(rows)):
            number, compound, age = rows[index]
            compound_before, age_before = rows[index - 1][1], ages[index - 1]
            if number <= 1:
                continue
            unnamed = (None, Compound.UNKNOWN)
            if (
                compound not in unnamed
                and compound_before not in unnamed
                and compound != compound_before
            ):
                changes += 1
                continue
            if age is None or age_before is None or age == age_before + 1:
                continue
            # A one-lap blip in the archive's age is no stop (the Singapore
            # sprint's #5 read 2, 3, 1, 5, 6): the age must carry on from the
            # restart, and one that continues from two laps back is no restart.
            two_back = ages[index - 2] if index >= 2 else None
            if two_back is not None and age == two_back + 2:
                continue
            after = ages[index + 1] if index + 1 < len(rows) else None
            if after is not None and after != age + 1:
                continue
            changes += 1
        result[driver] = changes
    return result


def _compare_keyed[K: Hashable, V](
    name: str,
    live: Mapping[K, V],
    historical: Mapping[K, V],
    *,
    same: Callable[[V, V], bool] = lambda a, b: a == b,
) -> Agreement:
    """`_compare_mappings` for any key (a driver, or a driver's lap) with
    its own notion of agreeing."""
    matched = 0
    mismatched = 0
    examples: list[str] = []
    for key in sorted(live.keys() & historical.keys(), key=repr):
        if same(live[key], historical[key]):
            matched += 1
            continue
        mismatched += 1
        if len(examples) < 5:
            label = f"driver {key[0]} lap {key[1]}" if isinstance(key, tuple) else f"driver {key}"
            examples.append(f"{label}: live {live[key]!r} vs historical {historical[key]!r}")
    return Agreement(
        name=name,
        matched=matched,
        mismatched=mismatched,
        only_live=len(live.keys() - historical.keys()),
        only_historical=len(historical.keys() - live.keys()),
        examples=tuple(examples),
    )


def _pair_by_time(
    live: Mapping[tuple[str, int], timedelta], archive: Mapping[tuple[str, int], timedelta]
) -> dict[tuple[str, int], tuple[str, int]]:
    """Archive lap -> the live lap with the same time (within
    `LAP_TIME_TOLERANCE`), per driver, each lap used once."""
    by_driver: dict[str, list[tuple[timedelta, tuple[str, int]]]] = {}
    for key, value in live.items():
        by_driver.setdefault(key[0], []).append((value, key))
    pairs: dict[tuple[str, int], tuple[str, int]] = {}
    for key, value in sorted(archive.items()):
        candidates = by_driver.get(key[0], [])
        match = next((c for c in candidates if abs(c[0] - value) <= LAP_TIME_TOLERANCE), None)
        if match is not None:
            candidates.remove(match)
            pairs[key] = match[1]
    return pairs


def _rekey[V](
    values: Mapping[tuple[str, int], V], pairs: Mapping[tuple[str, int], tuple[str, int]]
) -> dict[tuple[str, int], V]:
    return {pairs[key]: value for key, value in values.items() if key in pairs}


def compare_event_streams(
    session_key: SessionKey,
    live_events: list[DomainEvent],
    historical_events: list[DomainEvent],
) -> ComparisonReport:
    """What viewers were shown live (the store's event log, no state
    machine needed) against F1's archive of the same session: lap times,
    laps completed, the tyre and its age on every lap, stops, pit-in laps
    and the final order. Pure; see `f1_api.post_session` for which
    disagreements are worth an alert.

    In a race or sprint, lap N live is lap N in the archive. Its lap 1 is
    left out of the lap times: live times it from the start signal's
    receive time, an approximation the archive's own timing always
    disagrees with by more than the tolerance.

    In qualifying and practice the live lap counter runs a lap ahead of the
    archive's numbering (the 2026 Singapore sprint qualifying: Verstappen's
    pit laps 3, 6, 10, 16 live, 2, 5, 9, 15 in the archive), so laps are
    paired by their time instead, and the tyre on each is compared on the
    pairs. A live time with no partner is a time the archive never had."""
    live_times = _lap_times(live_events)
    archive_times = _lap_times(historical_events)
    archive_compounds = _compounds(historical_events)
    archive_ages = _tyre_ages(historical_events)
    if session_key.session_type in (SessionType.RACE, SessionType.SPRINT):
        live_times = {key: value for key, value in live_times.items() if key[1] > 1}
        archive_times = {key: value for key, value in archive_times.items() if key[1] > 1}
    else:
        pairs = _pair_by_time(live_times, archive_times)
        # An archive lap with no live partner keeps a key no live lap has
        # (its number negated), so it counts as missed, if competitive.
        fastest = min(archive_times.values(), default=None)
        archive_times = _rekey(archive_times, pairs) | {
            (key[0], -key[1]): value
            for key, value in archive_times.items()
            if key not in pairs and fastest is not None and value <= fastest * COMPETITIVE_LAP
        }
        archive_compounds = _rekey(archive_compounds, pairs)
        archive_ages = _rekey(archive_ages, pairs)
    return ComparisonReport(
        session_key=session_key,
        live_event_count=len(live_events),
        historical_event_count=len(historical_events),
        agreements=(
            _compare_lap_times(live_times, archive_times),
            _compare_keyed(
                "laps_completed", _laps_completed(live_events), _laps_completed(historical_events)
            ),
            _compare_keyed("compound", _compounds(live_events), archive_compounds),
            _compare_keyed(
                "tyre_age",
                _tyre_ages(live_events),
                archive_ages,
                same=lambda a, b: abs(a - b) <= TYRE_AGE_TOLERANCE,
            ),
            _compare_keyed("stops", _stops(live_events), _tyre_changes(historical_events)),
            _compare_mappings(
                "pit_laps", dict(_pit_laps(live_events)), dict(_pit_laps(historical_events))
            ),
            _compare_mappings(
                "final_positions",
                dict(_final_positions(live_events)),
                dict(_final_positions(historical_events)),
            ),
        ),
    )


def compare_session(
    recording_dir: Path, historical_root: Path, session_key: SessionKey
) -> ComparisonReport:
    """Run BOTH paths for one session and compare them. The live path
    replays the recording through the real Phase 1 pipeline; the historical
    path is `events_from_parquet`."""
    # Imported lazily so the pure `compare_streams` core (and its tests) need
    # neither f1_replay nor a recording present.
    from f1_historical_adapter.adapter import events_from_parquet
    from f1_parse import SessionStateMachine
    from f1_replay.replayer import replay
    from f1_replay.sinks import list_sink

    records: list[object] = []
    replay(recording_dir, list_sink(records), speed=0)  # type: ignore[arg-type]
    machine = SessionStateMachine(session_key=session_key)
    live_events: list[DomainEvent] = []
    for record in records:
        live_events.extend(machine.apply(record))  # type: ignore[arg-type]

    historical_events = list(events_from_parquet(historical_root, session_key))
    return compare_streams(session_key, live_events, machine.state, historical_events)
