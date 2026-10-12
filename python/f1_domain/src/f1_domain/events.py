"""The core domain events: the `DomainEvent` discriminated union.

This union is the Phase 1 contract — what the normalizer emits, what the
event log stores, what Phase 2's kernel consumes. Discrimination is on the
`kind` field, so any event serializes to/from JSON unambiguously
(`DOMAIN_EVENT_ADAPTER.validate_json(...)` picks the right class).

Envelope fields every event carries:
- `session_key`: which session this happened in.
- `ts_utc`: timezone-aware UTC time of the underlying observation.
- `source_seq`: the `f1_recording.RawRecord.seq` this event was derived
  from — the trace back to the exact wire message. Never fabricate it.
  (The historical adapter, which has no wire messages, sets a synthetic
  monotonic seq and marks `provenance=HISTORICAL` so it is never mistaken
  for a real one.)
- `provenance`: `LIVE` (default) vs `HISTORICAL` — see `Provenance`.

Optionality: the feed drops fields constantly, so almost every payload
field is `None`-able. The envelope itself is required — it comes from our
pipeline, not from the feed, so there is no excuse for it to be missing.

Units are explicit per field: times are `timedelta`, speeds km/h,
temperatures °C, pressure mbar, wind m/s and degrees.

`UnknownEvent` preserves any message the parser doesn't understand yet
(raw topic + payload, verbatim). The feed is unofficial and changes
without notice — data we can't parse today is still data; never drop it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from f1_domain.identity import Compound, DriverRef, Provenance, SessionKey, TrackStatusKind


class EventBase(BaseModel):
    """Envelope shared by every domain event. Not itself a member of the
    `DomainEvent` union — subclass it and add a `kind` Literal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_key: SessionKey
    ts_utc: datetime
    source_seq: int = Field(ge=0)
    provenance: Provenance = Provenance.LIVE
    """`LIVE` for events derived from the SignalR feed (live or replayed),
    `HISTORICAL` for events reconstructed from FastF1/OpenF1 tables by
    `f1_historical_adapter`. Defaults to `LIVE` so the live pipeline never
    sets it explicitly; the historical adapter stamps `HISTORICAL`."""


class LapCompleted(EventBase):
    """A driver crossed the line to complete a lap."""

    kind: Literal["lap_completed"] = "lap_completed"
    driver: str | None = None
    lap_number: int | None = None
    lap_time: timedelta | None = None
    sector_times: list[timedelta | None] | None = None
    is_personal_best: bool | None = None
    compound: Compound | None = None
    tyre_age: int | None = None
    """Tyre age in laps at the end of this lap."""


class SectorCompleted(EventBase):
    kind: Literal["sector_completed"] = "sector_completed"
    driver: str | None = None
    lap_number: int | None = None
    sector: int | None = None
    """1-based sector index (1, 2, 3)."""
    time: timedelta | None = None


class CarTelemetry(EventBase):
    """One car telemetry sample (a `CarData.z` batch fans out to many)."""

    kind: Literal["car_telemetry"] = "car_telemetry"
    driver: str | None = None
    speed: float | None = None
    """km/h."""
    rpm: int | None = None
    gear: int | None = None
    """0 = neutral."""
    throttle: float | None = None
    """Percent, 0-100 (the feed occasionally exceeds 100)."""
    brake: float | None = None
    """Percent, 0-100; some seasons send it as effectively boolean 0/100."""
    drs: int | None = None
    """Raw feed DRS code: 0/1 off, 8 eligible, 10/12/14 open."""


class CarPosition(EventBase):
    """One on-track position sample (a `Position.z` batch fans out to many)."""

    kind: Literal["car_position"] = "car_position"
    driver: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    """Track-local coordinates in the feed's own units (~1/10 m)."""
    status: str | None = None
    """e.g. "OnTrack" / "OffTrack", verbatim from the feed."""


class PositionChanged(EventBase):
    """A driver's classification position changed."""

    kind: Literal["position_changed"] = "position_changed"
    driver: str | None = None
    position: int | None = None


class GapUpdated(EventBase):
    kind: Literal["gap_updated"] = "gap_updated"
    driver: str | None = None
    gap_to_leader: timedelta | None = None
    """None when unknown OR when the driver is lapped (see `laps_down`)."""
    laps_down: int | None = None
    """Whole laps behind the leader when lapped (the feed sends "1 L" style
    strings — a count, never a duration). None when on the lead lap or
    unknown; `gap_to_leader` and `laps_down` are mutually exclusive."""
    interval_to_ahead: timedelta | None = None


class PitEntry(EventBase):
    kind: Literal["pit_entry"] = "pit_entry"
    driver: str | None = None
    lap_number: int | None = None


class PitExit(EventBase):
    kind: Literal["pit_exit"] = "pit_exit"
    driver: str | None = None
    lap_number: int | None = None


class StintStarted(EventBase):
    kind: Literal["stint_started"] = "stint_started"
    driver: str | None = None
    stint_number: int | None = None
    """1-based."""
    compound: Compound | None = None
    start_lap: int | None = None
    tyre_age_at_start: int | None = None
    """Laps already on the tyre when the stint began (>0 = used set)."""


class TrackStatusChanged(EventBase):
    kind: Literal["track_status_changed"] = "track_status_changed"
    status: TrackStatusKind | None = None
    message: str | None = None
    """The feed's human-readable message, verbatim (e.g. "AllClear")."""


class RaceControlMessage(EventBase):
    kind: Literal["race_control_message"] = "race_control_message"
    category: str | None = None
    """e.g. "Flag", "SafetyCar", "Drs", "Other" - verbatim."""
    message: str | None = None
    flag: str | None = None
    """e.g. "GREEN", "YELLOW", "DOUBLE YELLOW", "CHEQUERED" - verbatim."""
    scope: str | None = None
    """e.g. "Track", "Sector", "Driver" - verbatim."""
    driver: str | None = None
    lap: int | None = None


class WeatherSample(EventBase):
    kind: Literal["weather_sample"] = "weather_sample"
    air_temp: float | None = None
    """°C."""
    track_temp: float | None = None
    """°C."""
    humidity: float | None = None
    """Percent, 0-100."""
    pressure: float | None = None
    """mbar."""
    wind_speed: float | None = None
    """m/s."""
    wind_dir: float | None = None
    """Degrees, 0-359, meteorological (direction the wind comes FROM)."""
    rainfall: bool | None = None
    """True if rain is falling."""


class TeamRadioClip(EventBase):
    """A driver-pit radio exchange became available. Phase 4 (the radio
    corpus) is built on this event — keep `audio_url` stable.
    """

    kind: Literal["team_radio_clip"] = "team_radio_clip"
    driver: str | None = None
    audio_url: str | None = None
    """Absolute URL (or feed-relative path) of the audio file."""
    captured_at: datetime | None = None


class LapCounterUpdated(EventBase):
    """The race's own lap counter moved (the feed's `LapCount` topic).

    Distinct from `LapCompleted`, which is one DRIVER finishing one lap.
    This is the number on the timing screen: the lap the race is ON. A
    consumer that derives "current lap" from `max(LapCompleted.lap_number)`
    is always at least one lap stale, and stale by more whenever ingest is
    behind -- which is why this exists.
    """

    kind: Literal["lap_counter_updated"] = "lap_counter_updated"
    current_lap: int | None = None
    total_laps: int | None = None


class SessionStatusChanged(EventBase):
    kind: Literal["session_status_changed"] = "session_status_changed"
    status: str | None = None
    """e.g. "Inactive", "Started", "Aborted", "Finished", "Finalised", "Ends" - verbatim."""


class SessionClockUpdated(EventBase):
    """The session clock started, stopped or was restated (the feed's
    `ExtrapolatedClock`): the time left at `ts_utc`, and whether it is
    counting down. A consumer extrapolates between updates; the feed sends
    one only when the clock starts, stops or jumps, never every second.

    In qualifying it is the current part's clock (Q1 18:00, Q2 15:00, ...),
    stopped by a red flag; in a race, the time limit."""

    kind: Literal["session_clock_updated"] = "session_clock_updated"
    remaining: timedelta | None = None
    running: bool | None = None
    """True while the clock counts down from `remaining` (the feed's
    `Extrapolating`)."""


class QualifyingPartChanged(EventBase):
    """Qualifying moved into a new part: Q1, Q2 or Q3 (SQ1 to SQ3 in sprint
    qualifying). The feed announces the part a minute or so before its
    clock starts (`SessionPart`)."""

    kind: Literal["qualifying_part_changed"] = "qualifying_part_changed"
    part: int | None = None
    """1, 2 or 3."""
    entries: list[int] | None = None
    """How many cars take part in each part, e.g. [22, 16, 10]: the cut at
    the end of part N is `entries[N]` (the feed's `NoEntries`)."""


class QualifyingBestLap(EventBase):
    """A driver's best lap in one part of qualifying changed: a quicker lap,
    or the best deleted (track limits) and the next best standing again.
    Per part, because each part ranks only its own laps."""

    kind: Literal["qualifying_best_lap"] = "qualifying_best_lap"
    driver: str | None = None
    part: int | None = None
    lap_time: timedelta | None = None
    """None when no lap in this part stands any more."""
    lap_number: int | None = None


class QualifyingKnockout(EventBase):
    """A driver was knocked out of qualifying at the end of `part` (the
    feed's `KnockedOut`), or, rarely, reinstated (`knocked_out=False`)."""

    kind: Literal["qualifying_knockout"] = "qualifying_knockout"
    driver: str | None = None
    part: int | None = None
    knocked_out: bool = True


class SessionInfoUpdated(EventBase):
    """The feed named the meeting and its venue.

    In the log so a live session can be named, and matched to its circuit,
    from the store alone. Before this, a live session reached the API with
    no name at all, so it could never find a circuit and the track map was
    blank for exactly the races watched live. The venue's short name rides
    along because a Grand Prix name is not an identity: the "Spanish Grand
    Prix" ran at Barcelona through 2025 and at Madrid from 2026."""

    kind: Literal["session_info_updated"] = "session_info_updated"
    meeting_name: str | None = None
    """e.g. "Azerbaijan Grand Prix", verbatim."""
    circuit_short_name: str | None = None
    """`Meeting.Circuit.ShortName`: "Baku", "Madring", verbatim."""


class DriverListUpdated(EventBase):
    """The feed announced (or corrected) who is driving."""

    kind: Literal["driver_list_updated"] = "driver_list_updated"
    drivers: list[DriverRef] | None = None


class UnknownEvent(EventBase):
    """A raw message the parser could not map to any event above. The
    topic and payload are preserved verbatim so nothing is ever silently
    dropped — a later parser version can re-derive real events from the
    stored log.
    """

    kind: Literal["unknown"] = "unknown"
    topic: str
    payload: JsonValue = None


DomainEvent = Annotated[
    LapCompleted
    | SectorCompleted
    | CarTelemetry
    | CarPosition
    | PositionChanged
    | GapUpdated
    | PitEntry
    | PitExit
    | StintStarted
    | TrackStatusChanged
    | RaceControlMessage
    | WeatherSample
    | TeamRadioClip
    | SessionStatusChanged
    | DriverListUpdated
    | SessionInfoUpdated
    | LapCounterUpdated
    | SessionClockUpdated
    | QualifyingPartChanged
    | QualifyingBestLap
    | QualifyingKnockout
    | UnknownEvent,
    Field(discriminator="kind"),
]
"""The Phase 1 contract: every event the normalizer can emit."""

DOMAIN_EVENT_ADAPTER: TypeAdapter[DomainEvent] = TypeAdapter(DomainEvent)
"""Validate/serialize any `DomainEvent` from/to JSON, resolving `kind`."""
