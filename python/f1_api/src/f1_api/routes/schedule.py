"""The race calendar and circuits by id (ROADMAP W3): what the home page and
`/live` show between sessions, so no route is ever empty."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from f1_api.config import Settings, get_settings
from f1_api.convert import circuit_out
from f1_api.schedule import (
    ScheduleCache,
    ScheduledEvent,
    ScheduleUnavailableError,
    fastf1_schedule,
    fixture_schedule,
    next_event,
)
from f1_api.schemas import CircuitOut, ScheduleEventOut, ScheduleSessionOut
from f1_kernel import load_circuit
from f1_kernel.circuit import circuit_for_schedule

router = APIRouter(tags=["schedule"])

_caches: dict[str, ScheduleCache] = {}
_caches_lock = threading.Lock()


def get_schedule_cache(settings: Annotated[Settings, Depends(get_settings)]) -> ScheduleCache:
    """One cache per process and schedule source, so the daily refresh is
    shared by every request."""
    key = str(settings.schedule_fixture_path or f"fastf1:{settings.fastf1_cache_dir}")
    with _caches_lock:
        cache = _caches.get(key)
        if cache is None:
            provider = (
                fixture_schedule(settings.schedule_fixture_path)
                if settings.schedule_fixture_path is not None
                else partial(fastf1_schedule, cache_dir=settings.fastf1_cache_dir)
            )
            cache = ScheduleCache(provider)
            _caches[key] = cache
        return cache


ScheduleCacheDep = Annotated[ScheduleCache, Depends(get_schedule_cache)]


def _event_out(event: ScheduledEvent) -> ScheduleEventOut:
    circuit = circuit_for_schedule(event.event_name, event.location)
    return ScheduleEventOut(
        season=event.season,
        round=event.round,
        event_name=event.event_name,
        location=event.location,
        country=event.country,
        event_format=event.event_format,
        circuit_id=circuit.circuit_id if circuit is not None else None,
        circuit_name=circuit.name if circuit is not None else None,
        sessions=[
            ScheduleSessionOut(
                name=session.name,
                session_type=session.session_type.value if session.session_type else None,
                start_utc=session.start_utc,
                start_local=session.start_local,
            )
            for session in event.sessions
        ],
    )


def _events(cache: ScheduleCache, season: int) -> list[ScheduledEvent]:
    try:
        return cache.events(season)
    except ScheduleUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/schedule", response_model=list[ScheduleEventOut])
def get_schedule(
    cache: ScheduleCacheDep,
    season: Annotated[int | None, Query(ge=2018, le=2100)] = None,
) -> list[ScheduleEventOut]:
    """Every round of `season` (default: this year) with its sessions in UTC
    and circuit-local time. 503 when no schedule could be fetched yet."""
    year = season if season is not None else datetime.now(UTC).year
    return [_event_out(event) for event in _events(cache, year)]


@router.get("/schedule/next", response_model=ScheduleEventOut | None)
def get_next_event(cache: ScheduleCacheDep) -> ScheduleEventOut | None:
    """This weekend's round while its race may still be running, else the
    next to start, looking into next season after the last round. Null when
    neither season has anything left."""
    now = datetime.now(UTC)
    for year in (now.year, now.year + 1):
        try:
            events = cache.events(year)
        except ScheduleUnavailableError:
            if year == now.year:
                raise HTTPException(status_code=503, detail="schedule unavailable") from None
            continue
        upcoming = next_event(events, now)
        if upcoming is not None:
            return _event_out(upcoming)
    return None


@router.get("/circuits/{circuit_id}", response_model=CircuitOut)
def get_circuit_by_id(circuit_id: str) -> CircuitOut:
    """A shipped circuit's geometry by id, for a calendar entry that has no
    session yet (the home page's track drawing)."""
    try:
        return circuit_out(load_circuit(circuit_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown circuit {circuit_id!r}") from error
