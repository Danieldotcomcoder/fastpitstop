"""Track status as a TIMELINE, so any time window can be asked what the
track was doing.

Lap frames need "what status did this lap run under", which the event
stream does not answer directly — it carries status *changes*, and (for
historical sessions) sometimes not even those. This module folds both
sources into one interval list:

- **Live/replay**: `TrackStatusChanged` events are the direct signal, and
  when any are present they are used exclusively.
- **Historical**: the adapter emits no `TrackStatusChanged` at all (see
  DATA_CONTRACTS.md — history has no track-status delta stream), so the
  timeline is reconstructed from `RaceControlMessage`s, whose vocabulary is
  stable and well-known: `category="SafetyCar"` with DEPLOYED/ENDING/IN
  THIS LAP messages, and `category="Flag"` with track- or sector-scoped
  GREEN/YELLOW/DOUBLE YELLOW/RED/CLEAR.

Deliberate modelling choices, because they change what gets flagged:

- **Sector yellows are track state.** A yellow in one sector slows every
  car that passes it, so an active sector yellow puts the timeline in
  YELLOW. Sector flags are tracked as a SET and the timeline leaves yellow
  only when the last sector clears.
- **Driver-scoped flags are not.** BLUE and BLACK AND WHITE are addressed
  to one car (lapped traffic, track-limits warning); they say nothing about
  the track, and folding them in would flag half a race yellow.
- **"SAFETY CAR IN THIS LAP" does not end the safety car.** It announces
  that it comes in at the end of the current lap, so the interval stays
  open until the following track-scope CLEAR/GREEN — which is what race
  control actually calls when racing resumes.
- **Chequered is an instant, not a state.** It is ranked with green so the
  final lap of a race is not flagged as run under a non-green track.

Reconstructed-from-race-control timestamps come from OpenF1's absolute
clock while lap times come from FastF1's reconstructed session clock, so
alignment is approximate to a second or so (DATA_CONTRACTS.md). Safety-car
windows last minutes, so this does not affect which laps land inside them —
but a lap boundary within a second of a status change can fall either way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from f1_domain import DomainEvent, RaceControlMessage, TrackStatusChanged, TrackStatusKind

SEVERITY: dict[TrackStatusKind, int] = {
    TrackStatusKind.GREEN: 0,
    TrackStatusKind.CHEQUERED: 0,
    TrackStatusKind.YELLOW: 1,
    TrackStatusKind.VSC: 2,
    TrackStatusKind.SC: 3,
    TrackStatusKind.RED: 4,
}
"""How restrictive each status is. `status_during` reports the most severe
status overlapping a window, so a lap that ran partly yellow and partly
under the safety car reports `sc` (and `statuses_during` reports both)."""

_SECTOR_PATTERN = re.compile(r"SECTOR\s+(\d+)", re.IGNORECASE)
_YELLOW_FLAGS = frozenset({"YELLOW", "DOUBLE YELLOW"})
_CLEARING_FLAGS = frozenset({"CLEAR", "GREEN"})


@dataclass(frozen=True, slots=True)
class StatusInterval:
    """A period during which the track was in one status. `end_utc` is None
    for an interval still open at the end of the stream."""

    status: TrackStatusKind
    start_utc: datetime
    end_utc: datetime | None
    source: str
    """`"track_status"` (the feed's own topic) or `"race_control"`
    (reconstructed) — a provenance signal, since the reconstructed one is
    inferred from messages rather than observed directly."""

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Whether this interval intersects `[start, end]`. Touching
        endpoints count: a status that begins exactly as a lap ends did
        not affect that lap."""
        if self.end_utc is not None and self.end_utc <= start:
            return False
        return self.start_utc < end


def track_status_timeline(events: Iterable[DomainEvent]) -> list[StatusInterval]:
    """Build the status timeline for one session's events.

    Prefers `TrackStatusChanged` when the stream has any (the feed's own
    signal); otherwise reconstructs from race control. Never mixes the two,
    so a session with a partial track-status stream is not double-counted.
    """
    materialized = list(events)
    direct = [e for e in materialized if isinstance(e, TrackStatusChanged)]
    if direct:
        return _from_track_status(direct)
    return _from_race_control([e for e in materialized if isinstance(e, RaceControlMessage)])


def status_during(
    timeline: Iterable[StatusInterval], start: datetime, end: datetime
) -> TrackStatusKind:
    """The most severe status overlapping `[start, end]`, `GREEN` if none.

    Chequered ranks with green, so a window that only overlaps the chequered
    flag comes back GREEN — see the module docstring.
    """
    worst = TrackStatusKind.GREEN
    for interval in timeline:
        if interval.overlaps(start, end) and SEVERITY[interval.status] > SEVERITY[worst]:
            worst = interval.status
    return worst


def statuses_during(
    timeline: Iterable[StatusInterval], start: datetime, end: datetime
) -> list[TrackStatusKind]:
    """EVERY non-green status overlapping `[start, end]`, most severe first.

    `status_during` collapses a mixed lap to its worst status; this keeps
    all of them, which is what lets the clean-lap filter flag a lap that ran
    partly yellow AND partly under the safety car as both.
    """
    seen = {
        interval.status
        for interval in timeline
        if interval.overlaps(start, end) and SEVERITY[interval.status] > 0
    }
    return sorted(seen, key=lambda status: -SEVERITY[status])


def _from_track_status(events: list[TrackStatusChanged]) -> list[StatusInterval]:
    """One interval per status run: each event closes the previous one."""
    intervals: list[StatusInterval] = []
    ordered = sorted(events, key=lambda e: (e.ts_utc, e.source_seq))
    for event in ordered:
        if event.status is None:
            continue
        if intervals and intervals[-1].end_utc is None:
            previous = intervals[-1]
            if previous.status == event.status:
                continue  # a re-send of the same status, not a new interval
            intervals[-1] = StatusInterval(
                status=previous.status,
                start_utc=previous.start_utc,
                end_utc=event.ts_utc,
                source=previous.source,
            )
        if event.status is TrackStatusKind.GREEN:
            continue  # green is the implicit default; it closes, never opens
        intervals.append(
            StatusInterval(
                status=event.status,
                start_utc=event.ts_utc,
                end_utc=None,
                source="track_status",
            )
        )
    return intervals


class _RaceControlFold:
    """Folds race-control messages into intervals, tracking the safety car
    and the set of sectors currently under yellow independently (they
    overlap constantly: a yellow is what triggers the safety car)."""

    def __init__(self) -> None:
        self.intervals: list[StatusInterval] = []
        self._open: dict[TrackStatusKind, datetime] = {}
        self._yellow_sectors: set[int] = set()

    def open(self, status: TrackStatusKind, ts: datetime) -> None:
        self._open.setdefault(status, ts)

    def reset_yellow_sectors(self) -> None:
        self._yellow_sectors.clear()

    def add_yellow_sector(self, sector: int) -> None:
        self._yellow_sectors.add(sector)

    def clear_yellow_sector(self, sector: int) -> bool:
        """Drop one sector's yellow; True when none remain."""
        self._yellow_sectors.discard(sector)
        return not self._yellow_sectors

    def close(self, status: TrackStatusKind, ts: datetime) -> None:
        start = self._open.pop(status, None)
        if start is not None:
            self.intervals.append(
                StatusInterval(status=status, start_utc=start, end_utc=ts, source="race_control")
            )

    def finish(self) -> list[StatusInterval]:
        for status, start in self._open.items():
            self.intervals.append(
                StatusInterval(status=status, start_utc=start, end_utc=None, source="race_control")
            )
        self._open.clear()
        return sorted(self.intervals, key=lambda i: (i.start_utc, SEVERITY[i.status]))


def _from_race_control(events: list[RaceControlMessage]) -> list[StatusInterval]:
    fold = _RaceControlFold()
    for event in sorted(events, key=lambda e: (e.ts_utc, e.source_seq)):
        ts = event.ts_utc
        category = (event.category or "").strip().casefold()
        message = (event.message or "").strip().upper()
        flag = (event.flag or "").strip().upper()
        scope = (event.scope or "").strip().casefold()

        if category == "safetycar":
            _apply_safety_car(fold, message, ts)
            continue
        if category != "flag" or scope == "driver":
            # Driver-scoped flags (blue, black-and-white) are not track state.
            continue

        if flag == "RED":
            # A red flag supersedes a safety car: the race is stopped, not
            # neutralized. 2024 round 21 does exactly this, and leaving the
            # safety car open would neutralize the rest of the race.
            fold.close(TrackStatusKind.SC, ts)
            fold.close(TrackStatusKind.VSC, ts)
            fold.open(TrackStatusKind.RED, ts)
        elif scope == "sector":
            _apply_sector_flag(fold, flag, message, ts)
        elif flag in _CLEARING_FLAGS:
            # A track-wide clear ends EVERYTHING open: sector yellows whose
            # individual CLEAR messages we may not have seen, the red flag,
            # and the safety car. Across every 2024 race with a safety car,
            # `TRACK CLEAR` is only ever called once the car has already
            # been brought in — never mid-period — so closing here cannot
            # truncate a real one, and not closing here would leave a
            # safety car that ended some other way (a red flag, the
            # chequered flag) open for the rest of the session.
            fold.reset_yellow_sectors()
            fold.close(TrackStatusKind.YELLOW, ts)
            fold.close(TrackStatusKind.RED, ts)
            fold.close(TrackStatusKind.SC, ts)
            fold.close(TrackStatusKind.VSC, ts)
    return fold.finish()


def _apply_safety_car(fold: _RaceControlFold, message: str, ts: datetime) -> None:
    """SafetyCar-category messages. The vocabulary is small and stable:
    DEPLOYED, ENDING (virtual only), IN THIS LAP, and THROUGH THE PIT LANE
    (which changes nothing about the track state and is ignored)."""
    virtual = "VIRTUAL" in message
    status = TrackStatusKind.VSC if virtual else TrackStatusKind.SC
    if "DEPLOYED" in message:
        if not virtual:
            # A virtual safety car upgraded to a full one: the VSC period
            # is over, not running alongside it (2024 rounds 5 and 23).
            fold.close(TrackStatusKind.VSC, ts)
        fold.open(status, ts)
    elif "ENDING" in message:
        # The VSC ends immediately when race control says so.
        fold.close(status, ts)
    # "IN THIS LAP" deliberately does NOT close the interval: it announces
    # that the car comes in at the END of the current lap, so the track is
    # still neutralized until the following track-scope clear.


def _apply_sector_flag(fold: _RaceControlFold, flag: str, message: str, ts: datetime) -> None:
    match = _SECTOR_PATTERN.search(message)
    sector = int(match.group(1)) if match else -1
    if flag in _YELLOW_FLAGS:
        fold.add_yellow_sector(sector)
        fold.open(TrackStatusKind.YELLOW, ts)
    elif flag in _CLEARING_FLAGS and fold.clear_yellow_sector(sector):
        fold.close(TrackStatusKind.YELLOW, ts)
