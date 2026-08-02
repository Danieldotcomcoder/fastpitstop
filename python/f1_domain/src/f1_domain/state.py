"""Materialized state models: what "right now" looks like.

These are the shapes the Phase 1 state-builder maintains by folding
`DomainEvent`s, and what the materialized current-state tables mirror.
They are types only — the folding logic lives in the pipeline, not here.
Per CLAUDE.md's Phase 1 invariants, materialized state is a *projection*
of the append-only event log, never the only copy of anything.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from f1_domain.events import WeatherSample
from f1_domain.identity import Compound, DriverRef, SessionKey, TrackStatusKind


class DriverState(BaseModel):
    """One driver's current picture. Everything except identity is
    optional: early in a session (or after a red flag reset) most of this
    is simply not known yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    driver: DriverRef
    position: int | None = None
    last_lap: timedelta | None = None
    best_lap: timedelta | None = None
    current_compound: Compound | None = None
    tyre_age: int | None = None
    """Laps on the current set."""
    gap_to_leader: timedelta | None = None
    """None when unknown or lapped (see `GapUpdated`)."""
    laps_down: int | None = None
    """Whole laps behind the leader when lapped; None on the lead lap or
    unknown (mirrors `GapUpdated.laps_down`)."""
    interval: timedelta | None = None
    """Gap to the car ahead."""
    in_pit: bool | None = None
    stint_number: int | None = None
    laps_completed: int | None = None


class SessionState(BaseModel):
    """The whole session's current picture, keyed by racing number."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_key: SessionKey
    status: str | None = None
    """Verbatim session status (see `SessionStatusChanged.status`)."""
    current_lap: int | None = None
    """Leader's current lap (race/sprint only; None in practice/quali)."""
    track_status: TrackStatusKind | None = None
    weather: WeatherSample | None = None
    """The latest weather event, kept whole so its provenance
    (`ts_utc`/`source_seq`) travels with the reading."""
    drivers: dict[str, DriverState] = Field(default_factory=dict)
    """Racing number (as the feed's string key, e.g. "44") -> state."""
