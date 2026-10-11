"""The race calendar (ROADMAP W3, "never empty"): every round's sessions in
UTC and in the circuit's own time, from FastF1's event schedule, so the home
page and `/live` always know what is next.

The schedule holds no results, so it needs no spoiler gate. FastF1 sits
behind `ScheduleProvider` (the network is never touched by a test), and
`ScheduleCache` refreshes a season at most daily and keeps serving the last
good copy if a refresh fails: a calendar a day old beats none at all.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from f1_domain import SessionType

_log = logging.getLogger(__name__)

RACE_WINDOW = timedelta(hours=3)
"""How long after its start a race still counts as this weekend's: the
sporting regulations cap a race, suspensions included, at three hours."""

SCHEDULE_TTL_S = 24 * 3600
"""A season's schedule is refetched at most daily."""

_SESSION_TYPES: dict[str, SessionType] = {
    "practice 1": SessionType.PRACTICE_1,
    "practice 2": SessionType.PRACTICE_2,
    "practice 3": SessionType.PRACTICE_3,
    "qualifying": SessionType.QUALIFYING,
    "sprint qualifying": SessionType.SPRINT_QUALIFYING,
    "sprint shootout": SessionType.SPRINT_QUALIFYING,
    "sprint": SessionType.SPRINT,
    "race": SessionType.RACE,
}


class ScheduleUnavailableError(RuntimeError):
    """No schedule for the season could be fetched, and none is cached."""


@dataclass(frozen=True, slots=True)
class ScheduledSession:
    name: str
    """FastF1's name for the session ("Practice 1", "Sprint Qualifying")."""
    session_type: SessionType | None
    start_utc: datetime
    start_local: datetime | None
    """The same instant in the circuit's own time zone, offset included."""


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    season: int
    round: int
    event_name: str
    location: str
    country: str
    event_format: str
    sessions: tuple[ScheduledSession, ...]
    """In start order."""


ScheduleProvider = Callable[[int], list[ScheduledEvent]]


def session_type_for(name: str) -> SessionType | None:
    return _SESSION_TYPES.get(name.strip().casefold())


def _aware(value: Any) -> datetime | None:
    """A pandas/py datetime as an aware `datetime`, or None for NaT."""
    if value is None:
        return None
    try:
        converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    except (TypeError, ValueError):
        return None
    if not isinstance(converted, datetime):
        return None
    return converted


def fastf1_schedule(season: int, *, cache_dir: Path | None = None) -> list[ScheduledEvent]:
    """FastF1's event schedule for `season`, testing excluded."""
    import fastf1
    import pandas as pd

    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            fastf1.Cache.enable_cache(str(cache_dir))
        except OSError:
            _log.warning("FastF1 cache at %s is not writable; fetching uncached", cache_dir)
    frame = pd.DataFrame(fastf1.get_event_schedule(season, include_testing=False))
    events: list[ScheduledEvent] = []
    for _, row in frame.iterrows():
        sessions: list[ScheduledSession] = []
        for i in range(1, 6):
            name = row.get(f"Session{i}")
            utc = _aware(row.get(f"Session{i}DateUtc"))
            if not isinstance(name, str) or not name.strip() or utc is None:
                continue
            sessions.append(
                ScheduledSession(
                    name=name,
                    session_type=session_type_for(name),
                    start_utc=utc.replace(tzinfo=UTC) if utc.tzinfo is None else utc,
                    start_local=_aware(row.get(f"Session{i}Date")),
                )
            )
        events.append(
            ScheduledEvent(
                season=season,
                round=int(row["RoundNumber"]),
                event_name=str(row["EventName"]),
                location=str(row.get("Location") or ""),
                country=str(row.get("Country") or ""),
                event_format=str(row.get("EventFormat") or ""),
                sessions=tuple(sorted(sessions, key=lambda s: s.start_utc)),
            )
        )
    return sorted(events, key=lambda e: e.round)


def fixture_schedule(
    path: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> ScheduleProvider:
    """A schedule from a JSON file (`F1_API_SCHEDULE_FIXTURE`), for the
    browser tests: `{"<season>": [event, ...]}` with sessions carrying ISO
    8601 `start_utc` (and optionally `start_local`), or `start_in_h`, hours
    from the moment the file is read, so a test can always find a weekend in
    progress. The season key `"current"` stands for this year."""

    def start(raw: dict[str, Any]) -> datetime:
        if "start_in_h" in raw:
            return (now() + timedelta(hours=float(raw["start_in_h"]))).replace(microsecond=0)
        return datetime.fromisoformat(str(raw["start_utc"]))

    def provider(season: int) -> list[ScheduledEvent]:
        document: dict[str, list[dict[str, Any]]] = json.loads(path.read_text(encoding="utf-8"))
        current = now().year
        raw_events = document.get(str(season), []) + (
            document.get("current", []) if season == current else []
        )
        events: list[ScheduledEvent] = []
        for raw in raw_events:
            sessions = tuple(
                ScheduledSession(
                    name=str(s["name"]),
                    session_type=session_type_for(str(s["name"])),
                    start_utc=start(s),
                    start_local=(
                        datetime.fromisoformat(str(s["start_local"]))
                        if s.get("start_local")
                        else None
                    ),
                )
                for s in raw.get("sessions", [])
            )
            events.append(
                ScheduledEvent(
                    season=season,
                    round=int(raw["round"]),
                    event_name=str(raw["event_name"]),
                    location=str(raw.get("location", "")),
                    country=str(raw.get("country", "")),
                    event_format=str(raw.get("event_format", "conventional")),
                    sessions=tuple(sorted(sessions, key=lambda s: s.start_utc)),
                )
            )
        return events

    return provider


class ScheduleCache:
    """Each season fetched at most every `ttl_s`; on a failed refresh the
    last good copy is served. Thread-safe: FastAPI runs sync routes on a
    thread pool."""

    def __init__(
        self,
        provider: ScheduleProvider,
        *,
        ttl_s: float = SCHEDULE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._seasons: dict[int, tuple[float, list[ScheduledEvent]]] = {}

    def events(self, season: int) -> list[ScheduledEvent]:
        with self._lock:
            cached = self._seasons.get(season)
            if cached is not None and self._clock() - cached[0] < self._ttl_s:
                return cached[1]
            try:
                fresh = self._provider(season)
            except Exception as error:
                if cached is not None:
                    _log.warning("schedule refresh for %d failed; serving cached", season)
                    return cached[1]
                raise ScheduleUnavailableError(f"no schedule for {season}: {error}") from error
            self._seasons[season] = (self._clock(), fresh)
            return fresh

    def warm(self, season: int) -> None:
        """Fetch `season` on a background thread, so the first page view
        after a restart doesn't wait on the network."""

        def run() -> None:
            try:
                self.events(season)
            except ScheduleUnavailableError:
                _log.warning("schedule warm-up for %d failed", season, exc_info=True)

        threading.Thread(target=run, name=f"schedule-warm-{season}", daemon=True).start()


def next_event(events: Sequence[ScheduledEvent], now: datetime) -> ScheduledEvent | None:
    """This weekend's event while its race may still be running, otherwise
    the next one to start. `None` when the season has nothing left."""
    for event in sorted(events, key=lambda e: e.sessions[0].start_utc if e.sessions else now):
        if not event.sessions:
            continue
        if now < event.sessions[-1].start_utc + RACE_WINDOW:
            return event
    return None


__all__ = [
    "RACE_WINDOW",
    "SCHEDULE_TTL_S",
    "ScheduleCache",
    "ScheduleProvider",
    "ScheduleUnavailableError",
    "ScheduledEvent",
    "ScheduledSession",
    "fastf1_schedule",
    "fixture_schedule",
    "next_event",
    "session_type_for",
]
