"""Comparison harness: for a session with BOTH a Phase 0 recording and
historical parquet, run each path and report field-level agreement.

Discrepancies are EXPECTED — the two sources are genuinely different (live
is a raw delta stream; historical is FastF1's post-processed tables), so
this produces a REPORT, not an assertion. It exists to quantify how close
the seam is and to catch gross mismatches (wrong session, broken mapping).

The comparison itself (`compare_streams`) is a pure function of the two
event streams, so it is unit-testable without either data source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from f1_domain import DomainEvent, LapCompleted, PitEntry, PositionChanged, SessionKey, SessionState

# Lap times agree if within this tolerance. Live parses the feed's
# millisecond strings; historical is FastF1's own millisecond timing — a
# few ms of drift between them is not a discrepancy worth flagging.
LAP_TIME_TOLERANCE = timedelta(milliseconds=50)


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
