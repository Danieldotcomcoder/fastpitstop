"""Race context enrichment (Phase 4.3): WHAT was happening when a radio
clip was captured -- lap number, classification position, track status --
derived from Phase 1's own event stream, the join CLAUDE.md's Phase 4.3
prompt asks for ("this is what makes results meaningful ... and what
Phase 5 will consume").

`SessionContext` is built ONCE per session from its full `DomainEvent`
stream (the same "load, then pass data in" split `f1_kernel` and
`quality_report` already follow in this project) and then answers every
clip in that session's `at(driver, captured_at)` query in O(log n) --
`f1_radio.index_batch` builds one per session, not one per clip.

Track status reuses `f1_kernel.status.track_status_timeline`/`status_during`
rather than re-deriving it: historical sessions have no track-status delta
stream at all (DATA_CONTRACTS.md), so the SC/VSC/yellow reconstruction from
race control is exactly the logic the kernel's clean-lap filter already
depends on, and a radio clip captured mid-safety-car should agree with a
lap the kernel flags as run under one.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime

from f1_domain import DomainEvent, LapCompleted, PositionChanged, TrackStatusKind
from f1_kernel import status_during, statuses_during, track_status_timeline


@dataclass(frozen=True, slots=True)
class ClipContext:
    lap_number: int | None
    """The lap in progress when the clip was captured -- one past the last
    lap this driver had COMPLETED at `captured_at` (or lap 1 if none had).
    `None` when `driver`/`captured_at` was itself unknown."""
    position: int | None
    """This driver's classification position as of the last known update
    at or before `captured_at`. `None` if no position was known yet."""
    track_status: TrackStatusKind | None
    """The most severe track status overlapping the capture instant,
    `GREEN` if none, `None` only when `captured_at` itself was unknown --
    matching `f1_kernel.status.status_during`'s own convention."""
    track_status_flags: list[TrackStatusKind]
    """Every non-green status overlapping the capture instant -- lets a
    clip captured while a sector yellow coincided with a full safety car
    report both, the same distinction `status_during`/`statuses_during`
    draw for the kernel's own lap frame."""


_UNKNOWN_CONTEXT = ClipContext(
    lap_number=None, position=None, track_status=None, track_status_flags=[]
)


class SessionContext:
    """Answers `at(driver, captured_at)` for every clip in one session,
    built once from that session's full event stream."""

    def __init__(self, events: list[DomainEvent] | tuple[DomainEvent, ...]) -> None:
        materialized = list(events)
        self._lap_ts: dict[str, list[datetime]] = {}
        self._lap_no: dict[str, list[int]] = {}
        self._pos_ts: dict[str, list[datetime]] = {}
        self._pos_val: dict[str, list[int]] = {}

        lap_events: list[LapCompleted] = []
        position_events: list[PositionChanged] = []
        for event in materialized:
            if isinstance(event, LapCompleted) and event.driver and event.lap_number is not None:
                lap_events.append(event)
            elif isinstance(event, PositionChanged) and event.driver and event.position is not None:
                position_events.append(event)

        for event in sorted(lap_events, key=lambda e: (e.ts_utc, e.source_seq)):
            driver = event.driver
            assert driver is not None and event.lap_number is not None  # narrowed above
            self._lap_ts.setdefault(driver, []).append(event.ts_utc)
            self._lap_no.setdefault(driver, []).append(event.lap_number)

        for event in sorted(position_events, key=lambda e: (e.ts_utc, e.source_seq)):
            driver = event.driver
            assert driver is not None and event.position is not None  # narrowed above
            self._pos_ts.setdefault(driver, []).append(event.ts_utc)
            self._pos_val.setdefault(driver, []).append(event.position)

        self._status_timeline = track_status_timeline(materialized)

    def at(self, driver: str | None, captured_at: datetime | None) -> ClipContext:
        if driver is None or captured_at is None:
            return _UNKNOWN_CONTEXT
        return ClipContext(
            lap_number=self._lap_in_progress(driver, captured_at),
            position=self._last_value_at_or_before(
                self._pos_ts.get(driver, []), self._pos_val.get(driver, []), captured_at
            ),
            track_status=status_during(self._status_timeline, captured_at, captured_at),
            track_status_flags=statuses_during(self._status_timeline, captured_at, captured_at),
        )

    def _lap_in_progress(self, driver: str, captured_at: datetime) -> int:
        timestamps = self._lap_ts.get(driver, [])
        lap_numbers = self._lap_no.get(driver, [])
        completed_idx = bisect.bisect_right(timestamps, captured_at) - 1
        if completed_idx < 0:
            return 1  # no lap completed yet -- the clip happened during lap 1
        return lap_numbers[completed_idx] + 1

    def _last_value_at_or_before(
        self, timestamps: list[datetime], values: list[int], captured_at: datetime
    ) -> int | None:
        idx = bisect.bisect_right(timestamps, captured_at) - 1
        if idx < 0:
            return None
        return values[idx]
