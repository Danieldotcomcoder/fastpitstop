"""The public frame API: give it a source and a session, get typed frames.

`f1_kernel.frames` holds the pure event->frame transforms and
`f1_kernel.sources` holds the loading; this is the thin layer that joins
them, and it is what most callers actually use:

    from f1_kernel import HistoricalSource, lap_frame
    laps = lap_frame(HistoricalSource("data/historical"), key)

Each builder asks the source only for the event kinds it reads. For a
database or a parquet root that is the difference between loading a race's
laps and loading a race's telemetry to throw it away.
"""

from __future__ import annotations

import polars as pl

from f1_domain import SessionKey
from f1_kernel.circuit import circuit_for_event
from f1_kernel.frames import (
    SessionFrames,
    lap_frame_from_events,
    session_frames_from_events,
    stint_frame_from_events,
    telemetry_frame_from_events,
)
from f1_kernel.sources import LAP_FRAME_KINDS, EventSource

_TELEMETRY_KINDS = frozenset({*LAP_FRAME_KINDS, "car_telemetry"})


def lap_frame(source: EventSource, session_key: SessionKey) -> pl.DataFrame:
    """One row per completed lap — see `schemas.LAP_FRAME_SCHEMA`."""
    return lap_frame_from_events(source.events(session_key, kinds=LAP_FRAME_KINDS), session_key)


def stint_frame(source: EventSource, session_key: SessionKey) -> pl.DataFrame:
    """One row per (driver, stint) — see `schemas.STINT_FRAME_SCHEMA`."""
    events = list(source.events(session_key, kinds=LAP_FRAME_KINDS))
    return stint_frame_from_events(events, session_key)


def telemetry_frame(source: EventSource, session_key: SessionKey, driver: str) -> pl.DataFrame:
    """One driver's telemetry samples — see `schemas.TELEMETRY_FRAME_SCHEMA`.

    The driver filter is pushed into the source, so this loads one car's
    samples rather than the field's. Lap tagging needs that driver's laps
    too, which is why the lap kinds come along.
    """
    events = list(source.events(session_key, kinds=_TELEMETRY_KINDS, drivers=[driver]))
    return telemetry_frame_from_events(events, session_key, driver)


def session_frames(
    source: EventSource,
    session_key: SessionKey,
    *,
    event_name: str | None = None,
) -> SessionFrames:
    """Lap + stint frames and circuit metadata for one session, in a single
    pass over the events.

    `event_name` is what resolves the circuit; when omitted it is looked up
    from the source's own session list (the historical catalog carries it).
    A source that cannot enumerate its sessions simply yields no circuit —
    an analytic must already handle a circuit we do not ship.
    """
    resolved = event_name if event_name is not None else _event_name(source, session_key)
    events = list(source.events(session_key, kinds=LAP_FRAME_KINDS))
    frames = session_frames_from_events(events, session_key, event_name=resolved)
    if frames.circuit is None and resolved is not None:
        # Keep the resolved name even when no circuit file matches it.
        return SessionFrames(
            session_key=frames.session_key,
            laps=frames.laps,
            stints=frames.stints,
            circuit=circuit_for_event(resolved),
            event_name=resolved,
        )
    return frames


def _event_name(source: EventSource, session_key: SessionKey) -> str | None:
    try:
        refs = source.sessions()
    except Exception:
        return None
    for ref in refs:
        if ref.session_key == session_key:
            return ref.event_name
    return None
