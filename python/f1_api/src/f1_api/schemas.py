"""Pydantic response models — the Phase 3 interface (CLAUDE.md: "Get the
response contracts right first").

Every model here mirrors a kernel type field-for-field rather than
inventing its own shape: `LapRowOut` is `f1_kernel.ANNOTATED_LAP_FRAME_SCHEMA`,
`DegradationFitOut` is `f1_kernel.DegradationFit`, `CrossoverResultOut` is
`f1_kernel.CrossoverResult`, and so on. `f1_api.convert` turns the kernel's
own dataclasses/DataFrame rows into these with no logic of its own —
this package reads the kernel's output, it does not reshape it.

`f1_domain.SessionKey` is reused directly as a response field (it is
already a plain pydantic model), rather than redeclared here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from f1_domain import SessionKey
from f1_kernel import Confidence, CrossoverKind, Reliability, WindowState

Provenance = Literal["live", "historical"]


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyOut(BaseModel):
    """`GET /readyz` — unlike `/health`/`/healthz`, this one actually touches
    the database, so a 200 here means "safe to route traffic here", not just
    "the process is alive"."""

    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"
    last_ingest_at: datetime | None = None
    """`max(events.ts_utc)` across the store — the newest event timestamp
    any pipeline run has written, as a proxy for "last successful ingest".
    `None` is a normal state (a fresh database with no ingested session
    yet), not a failure."""


class ServerTimeOut(BaseModel):
    """`GET /time` — the server's wall clock, epoch milliseconds."""

    server_time_ms: int


class VersionOut(BaseModel):
    """`GET /version` — image build metadata, read from the `GIT_SHA`/
    `BUILD_TIME` env vars the Dockerfiles set. Surfaced on the `/status`
    page so "what's actually running" is a page load away, not a `docker
    inspect`."""

    git_sha: str
    build_time: str


class SessionSummaryOut(BaseModel):
    """One row of `GET /sessions`. `key` is the exact path segment to use
    against every `GET /sessions/{key}/...` route below."""

    key: str
    session_key: SessionKey
    event_name: str | None
    provenance: Provenance
    circuit_name: str | None = None
    """Human circuit name from `f1_kernel.circuit_for_event`, when known."""
    winner: str | None = None
    """P1 driver code when cheaply known; null on the list endpoint rather
    than loading every session's laps (detail routes compute classification)."""
    has_radio: bool = False
    has_telemetry: bool = False
    live_recorded: bool = False
    """True when this entry is backed by a live/store ingest. Measured car
    positions are NOT implied either way: the live feed withholds them, and
    they reach a replay from the `car_positions` table (backfilled, or
    imported from F1's archive after a live session)."""
    live: bool = False
    """True only while this session is actually RUNNING.

    Distinct from `provenance`/`live_recorded`, which stay true forever once
    a session has been ingested. A client deciding whether to open a
    `mode=live` stream must read this one."""


class ClassificationEntryOut(BaseModel):
    """One driver's row in `GET /sessions/{key}`'s classification — the
    final classification for a completed historical session, or the
    current running order for a live one. Derived uniformly from the lap
    frame's last lap per driver (see `f1_api.convert.classification_from_laps`),
    so historical and live sessions answer this the same way."""

    driver: str
    full_name: str | None = None
    code: str | None = None
    team: str | None = None
    position: int | None
    last_lap_s: float | None
    best_lap_s: float | None
    gap_to_leader_s: float | None
    compound: str | None
    tyre_age: int | None
    in_pit: bool | None
    laps_completed: int | None


class SessionDetailOut(BaseModel):
    """`GET /sessions/{key}`: metadata plus classification."""

    key: str
    session_key: SessionKey
    event_name: str | None
    provenance: Provenance
    status: str | None
    current_lap: int | None
    track_status: str | None
    classification: list[ClassificationEntryOut]


class LapRowOut(BaseModel):
    """One row of `GET /sessions/{key}/laps` — `f1_kernel.annotate_laps`'
    output: the lap frame plus every clean-lap exclusion reason, never
    silently filtered (CLAUDE.md's Phase 2 clean-lap-filter invariant)."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    lap_number: int
    lap_start_utc: datetime | None
    lap_end_utc: datetime | None
    lap_time_s: float | None
    lap_time_fuel_corrected_s: float | None = None
    """`lap_time_s` corrected to the kernel's reference fuel load
    (`f1_kernel.fuel_correct`), over the whole race's distance: the same
    units the degradation fits (and each kernel snapshot's stint fit) are
    in, so a chart can draw points and fit on one axis."""
    sector1_s: float | None
    sector2_s: float | None
    sector3_s: float | None
    is_personal_best: bool | None
    compound: str | None
    tyre_age: int | None
    stint_number: int | None
    position: int | None
    gap_ahead_s: float | None
    gap_ahead_min_s: float | None
    gap_ahead_samples: int | None
    gap_to_leader_s: float | None
    track_status: str | None
    track_status_flags: list[str]
    is_pit_in: bool | None
    is_pit_out: bool | None
    provenance: str
    # -- annotate_laps additions -----------------------------------------
    is_lap1: bool
    is_in_lap: bool
    is_out_lap: bool
    is_sc: bool
    is_vsc: bool
    is_yellow: bool
    is_red: bool
    is_dirty_air: bool
    is_anomalous: bool
    dirty_air_known: bool
    anomaly_known: bool
    is_clean: bool
    exclusion_reasons: list[str]


class DegradationFitOut(BaseModel):
    """One `f1_kernel.DegradationFit` — one (driver, stint, compound)."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    stint_number: int
    compound: str
    deg_rate_s_per_lap: float
    deg_rate_ci_low_s_per_lap: float
    deg_rate_ci_high_s_per_lap: float
    intercept_s: float
    quadratic_term_s_per_lap2: float | None
    n_laps_used: int
    r2: float
    reliability: Reliability


class DriverStintStateOut(BaseModel):
    """One driver's fitted state at a crossover's decision lap —
    `f1_kernel.DriverStintState`, the evidence behind a `CrossoverResultOut`."""

    driver: str
    stint_number: int | None
    compound: str | None
    tyre_age: int | None
    deg_rate_s_per_lap: float | None
    deg_rate_ci_low_s_per_lap: float | None
    deg_rate_ci_high_s_per_lap: float | None
    intercept_s: float | None
    pace_now_s: float | None
    n_laps_in_fit: int | None
    fit_reliability: str | None


class CrossoverResultOut(BaseModel):
    """One `f1_kernel.CrossoverResult` — an undercut/overcut projection for
    one (attacker, defender) pair. The Phase 3 countdown primitive."""

    kind: CrossoverKind
    attacker: str
    defender: str
    decision_lap: int
    attacker_stop_lap: int | None
    defender_stop_lap: int | None
    gap_s: float | None
    pit_loss_s: float
    pit_loss_provenance: str
    out_lap_penalty_s: float
    crossover_lap: int | None
    projected_gap_at_crossover_s: float | None
    projected_gap_if_covered_s: float | None
    projected_gap_if_covered_low_s: float | None
    projected_gap_if_covered_high_s: float | None
    projected_margin_at_assumed_stop_s: float | None
    projected_rejoin_gap_s: float | None
    window: WindowState
    opens_in_laps: int | None
    horizon_lap: int
    confidence: Confidence
    reasons: list[str]
    attacker_state: DriverStintStateOut
    defender_state: DriverStintStateOut


class BrakingDriftOut(BaseModel):
    """One `f1_kernel.BrakingDrift` — one (driver, stint, corner)'s fitted
    braking-point trend."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    stint_number: int
    compound: str
    corner_number: int
    corner_letter: str | None
    drift_m_per_lap: float
    drift_ci_low_m_per_lap: float
    drift_ci_high_m_per_lap: float
    intercept_m: float
    n_laps_used: int
    reliability: Reliability


class BrakingPointRowOut(BaseModel):
    """One row of `GET /sessions/{key}/telemetry/{driver}` —
    `f1_kernel.braking_points`' output: one (lap, corner) braking-point
    observation, the novel dataset CLAUDE.md's Phase 2.6 built instead of
    needing computer vision."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    lap_number: int
    corner_number: int
    corner_letter: str | None
    corner_distance_m: float
    brake_point_distance_m: float | None
    distance_before_corner_m: float | None
    n_samples_in_window: int
    known: bool


class LapSummaryRowOut(BaseModel):
    """One driver's row of `KernelReportOut.lap_summary` —
    `f1_kernel.clean_lap_summary`: how many laps survived the clean-lap
    filter and why the rest didn't."""

    driver: str
    laps: int
    clean_laps: int
    clean_fraction: float
    is_lap1: int
    is_in_lap: int
    is_out_lap: int
    is_sc: int
    is_vsc: int
    is_yellow: int
    is_red: int
    is_dirty_air: int
    is_anomalous: int


class DegradationByCompoundRowOut(BaseModel):
    """One compound's row of `KernelReportOut.degradation_by_compound` —
    `f1_kernel.summarize_degradation_by_compound`."""

    compound: str
    n_fits: int
    mean_deg_rate_s_per_lap: float | None = None
    median_deg_rate_s_per_lap: float | None = None
    std_deg_rate_s_per_lap: float | None = None
    min_deg_rate_s_per_lap: float | None = None
    max_deg_rate_s_per_lap: float | None = None
    n_good: int | None = None
    n_thin: int | None = None
    n_unreliable: int | None = None


class KernelReportOut(BaseModel):
    """`GET /sessions/{key}/analysis` — `f1_kernel.KernelReport`, the whole
    kernel behind one call. Per-lap frames (`annotated_laps`/`clean_laps`/
    `fuel_corrected_laps`) are deliberately NOT repeated here — `GET
    .../laps` already serves those rows; duplicating a few hundred rows of
    the same laps at three processing stages would triple the payload for
    no information a client can't already reconstruct from `/laps` plus
    this report's aggregates. Everything analytic-shaped is included."""

    session_key: SessionKey
    event_name: str | None
    provenance: Provenance
    total_laps: int | None
    decision_lap: int | None
    lap_summary: list[LapSummaryRowOut]
    degradation_fits: list[DegradationFitOut]
    degradation_by_compound: list[DegradationByCompoundRowOut]
    crossovers: list[CrossoverResultOut]
    braking_drift: list[BrakingDriftOut]
    braking_drivers: list[str]
    notes: list[str]
    summary: str


class CornerOut(BaseModel):
    """One `f1_kernel.Corner` — a numbered corner along the reference lap."""

    number: int
    letter: str | None
    distance_m: float
    angle_deg: float | None


class DrsZoneOut(BaseModel):
    """One `f1_kernel.DrsZone` — an activation zone in reference-lap distance."""

    number: int
    detection_m: float | None
    start_m: float
    end_m: float
    observed_drivers: int | None
    wraps_start_finish: bool


class TrackOutlineOut(BaseModel):
    """`f1_kernel.TrackOutline` — the real 2D lap shape Phase 3.4's track map
    renders, derived from position telemetry by `tools/build_circuits.py`."""

    points: list[tuple[float, float]]
    width_m: float
    height_m: float
    source_points: int


class PitLaneOut(BaseModel):
    """`f1_kernel.PitLane` — the pit lane as an open polyline from entry to
    exit, derived from the reference race's real pit stops, in the same
    units as the outline."""

    points: list[tuple[float, float]]
    length_m: float
    source_stops: int


class ScheduleSessionOut(BaseModel):
    """One session of a calendar round (`f1_api.schedule.ScheduledSession`)."""

    name: str
    """FastF1's name: "Practice 1", "Sprint Qualifying", "Race"."""
    session_type: str | None
    """Our `SessionType` value, or null for a session we don't model."""
    start_utc: datetime
    start_local: datetime | None
    """The same instant in the circuit's own time, with its UTC offset."""


class ScheduleEventOut(BaseModel):
    """`GET /schedule` and `GET /schedule/next`: one round of the calendar.
    Holds no results, so it needs no spoiler gate."""

    season: int
    round: int
    event_name: str
    location: str
    country: str
    event_format: str
    """FastF1's "conventional", "sprint_qualifying", ..."""
    circuit_id: str | None
    """The shipped circuit it runs at (`f1_kernel.circuit_for_schedule`), or
    null for a venue no circuit file knows yet."""
    circuit_name: str | None
    sessions: list[ScheduleSessionOut]


class CircuitOut(BaseModel):
    """`GET /sessions/{key}/circuit` — `f1_kernel.Circuit`'s geometry, for
    the track map. `None` (not a 404) when the session's event does not
    match one of the few circuits this project ships metadata for yet —
    the same "an analytic must degrade gracefully, not assume every venue
    is present" discipline `f1_kernel.circuit_for_event` already documents."""

    circuit_id: str
    name: str
    lap_distance_m: float
    corners: list[CornerOut]
    drs_zones: list[DrsZoneOut]
    outline: TrackOutlineOut | None
    pit_lane: PitLaneOut | None = None
    geometry_from_session: bool = False
    """True when `outline` and `pit_lane` were derived from this session's
    own measured positions, because the circuit file's (from another
    season's race) did not fit them."""


class TyreWearOut(BaseModel):
    """`f1_kernel.TyreWear`: one compound's measured wear at a circuit."""

    compound: str
    median_s_per_lap: float
    good_fits: int
    """Reliable stint fits behind the median: fewer than a handful is thin."""
    sessions: list[str]


class TyrePlanOut(BaseModel):
    compounds: list[str]
    cars: int


class RaceStrategyOut(BaseModel):
    """How a race was run (`f1_api.circuits.race_strategy`): the finishers'
    stops and tyre plans. Never a result."""

    finishers: int
    stops: dict[str, int]
    """Stops made -> finishers who made that many."""
    plans: list[TyrePlanOut]


class CircuitRaceOut(BaseModel):
    key: str
    season: int
    round: int
    event_name: str | None
    strategy: RaceStrategyOut | None


class CircuitSummaryOut(BaseModel):
    """`GET /circuits`: one shipped circuit, for the circuits index."""

    circuit_id: str
    name: str
    country: str
    lap_distance_m: float
    corners: int
    drs_zones: int
    pit_loss_s: float
    pit_loss_provenance: str
    pit_loss_uncertainty_s: float | None
    pit_loss_n_stops: int | None
    outline: TrackOutlineOut | None


class CircuitProfileOut(CircuitOut):
    """`GET /circuits/{circuit_id}`: the circuit page. Its geometry (as
    `CircuitOut`) plus the measured figures and the races held there."""

    country: str
    pit_loss_s: float
    pit_loss_provenance: str
    pit_loss_uncertainty_s: float | None
    pit_loss_n_stops: int | None
    tyre_wear: list[TyreWearOut]
    races: list[CircuitRaceOut]


class StandingsRoundOut(BaseModel):
    """A round with Grand Prix results, for the "last race you watched"
    choice. Names a race, never its result."""

    round: int
    event_name: str | None
    sprint: bool
    race_start_utc: datetime | None
    """When its Grand Prix started, from the calendar: the page keeps a race
    from the last few days hidden until the viewer says they've seen it."""


class DriverStandingOut(BaseModel):
    position: int
    code: str
    name: str
    """Family name ("Verstappen"), or the code when the results lack one."""
    team: str | None
    """The team of their latest counted Grand Prix."""
    team_color: str | None
    points: float
    wins: int
    by_round: list[float]
    """Running total after each counted round, in round order."""


class ConstructorStandingOut(BaseModel):
    position: int
    team: str
    team_color: str | None
    points: float
    wins: int
    by_round: list[float]


class RemainingPointsOut(BaseModel):
    """What one driver could still score after the counted rounds, from the
    calendar: every Grand Prix and sprint still to run, at its maximum."""

    races: int
    sprints: int
    race_points: int
    """The most a driver can score in one Grand Prix that season."""
    sprint_points: int
    max_points: int


class StandingsOut(BaseModel):
    """`GET /standings`: the championship after a round the viewer chose
    (`after_round`), so no later race reaches the page."""

    season: int
    after_round: int
    """The last round counted; 0 when none is."""
    rounds: list[StandingsRoundOut]
    """Every round of the season with results, counted or not."""
    drivers: list[DriverStandingOut]
    constructors: list[ConstructorStandingOut]
    remaining: RemainingPointsOut | None
    """Null when the calendar can't be read."""


class RadioSegmentOut(BaseModel):
    """One `f1_radio.transcription.TranscriptSegment`, stored as a plain
    dict on `radio_transcripts.segments` and reshaped here field-for-field."""

    start_s: float
    end_s: float
    text: str
    confidence: float


class RadioClipOut(BaseModel):
    """One team-radio exchange — the join `f1_radio.domain.radio_clip`
    already makes at the library layer, reshaped for the API: the
    originating `TeamRadioClip` event's own fields (`driver`, `captured_at`,
    `audio_url`) plus whatever `radio_transcripts` row exists for it.
    `has_transcript=False` with every transcript field `None` is a normal,
    expected state (Phase 4's own "transcription runs offline/batch" — a
    clip fetched but not yet transcribed), never an error. `audio_url` is
    the feed's own clip URL — the frontend fetches audio lazily from it when
    the user plays a clip; this API never streams audio bytes. `clip_id` is
    `f1_api.radio.clip_id_path`'s own opaque path segment, included here so
    a client (the corpus search page, whose `RadioSearchResultOut.clip`
    never carries `audio_url`) can resolve `GET /radio/{clip_id}` for one
    result without reimplementing that encoding itself."""

    session_key: SessionKey
    driver: str | None
    captured_at: datetime | None
    clip_id: str
    audio_url: str | None
    has_transcript: bool
    transcript_text: str | None
    language: str | None
    confidence: float | None
    model_version: str | None
    segments: list[RadioSegmentOut] | None
    lap_number: int | None
    position: int | None
    track_status: str | None
    track_status_flags: list[str] | None


class RadioSearchResultOut(BaseModel):
    """One `GET /radio/search` result: a clip plus this query's own
    similarity score. `clip.audio_url` is always `None` here — a search can
    span many sessions, and resolving each result's originating event would
    mean re-reading every one of those sessions' event streams just to
    answer a ranked list; `GET /radio/{clip_id}` (single-session-scoped) is
    where a client gets the playable URL, once a result is actually chosen."""

    clip: RadioClipOut
    similarity: float


class AnomalyOut(BaseModel):
    """One `GET /sessions/{key}/anomalies` row — `f1_why.Anomaly` field-for-
    field, plus the opaque `anomaly_id` path segment for
    `GET /anomalies/{id}/explanation`. Machine-readable only: `summary` is
    the detector's own numbers, never prose (narration is the explanation
    endpoint's job)."""

    anomaly_id: str
    kind: str
    lap_number: int | None
    anomaly_time: datetime
    drivers: list[str]
    magnitude: float
    salience: float
    summary: dict[str, float | int | str | bool | None]
    reasons: list[str]


class CitedEvidenceOut(BaseModel):
    """One evidence item an explanation actually cited — enough for a
    citation trail, with an optional `href` the client follows for full
    detail (radio clip / degradation / laps). Items with no dedicated REST
    route (anomaly facts, race-control messages) carry `href=None`."""

    evidence_id: str
    kind: str
    event_time: datetime
    drivers: list[str]
    summary: str
    confidence: float
    confidence_label: str
    href: str | None


class ExplanationOut(BaseModel):
    """`GET /anomalies/{id}/explanation` — the grounded narration plus the
    cited evidence trail. `anomaly_id` is the same opaque path segment the
    list endpoint returns; `text` is either a real cited sentence or
    `f1_why.FALLBACK_TEXT` when there is no clear cause."""

    anomaly_id: str
    session_key: SessionKey
    kind: str
    lap_number: int | None
    drivers: list[str]
    anomaly_time: datetime
    text: str
    confidence: Literal["high", "medium", "low"]
    grounded: bool
    fallback_reason: str | None
    cited_evidence: list[CitedEvidenceOut]


class StoryMomentOut(BaseModel):
    """One entry of `GET /sessions/{key}/story` — an anomaly together with
    its grounded explanation, resolved in the SAME pass.

    Exists because the alternative the frontend used to do (list anomalies,
    then one `GET /anomalies/{id}/explanation` per row) re-ran the whole
    `analyze_session` + `detect_anomalies` chain once per request: seconds
    of work per row, times a few hundred rows. Detection is a per-session
    computation, so the story is served as a per-session read.

    `explanation` is `None` only when narration for that moment could not
    be produced at all; a moment with no clear cause still carries a real
    `ExplanationOut` whose `text` is the canonical fallback and whose
    `grounded` is False — the honest "we don't know" the UI renders
    differently, never a missing row."""

    anomaly: AnomalyOut
    explanation: ExplanationOut | None


class StreamEnvelope(BaseModel):
    """The WebSocket wire format for `/stream/{key}`. `event_time_utc` is
    never stripped (Phase 3's Delay Sync gates every value on it), `kind`
    names the payload's shape (a `DomainEvent.kind`, `"kernel_snapshot"`,
    `"radio_clip"`, or `"explanation"`), and `payload` carries that shape's
    own fields verbatim."""

    event_time_utc: datetime
    kind: str
    payload: dict[str, Any]


__all__ = [
    "AnomalyOut",
    "BrakingDriftOut",
    "BrakingPointRowOut",
    "CircuitOut",
    "CitedEvidenceOut",
    "ClassificationEntryOut",
    "CornerOut",
    "CrossoverResultOut",
    "DegradationByCompoundRowOut",
    "DegradationFitOut",
    "DriverStintStateOut",
    "DrsZoneOut",
    "ExplanationOut",
    "HealthOut",
    "KernelReportOut",
    "LapRowOut",
    "LapSummaryRowOut",
    "Provenance",
    "RadioClipOut",
    "RadioSearchResultOut",
    "RadioSegmentOut",
    "ReadyOut",
    "ServerTimeOut",
    "SessionDetailOut",
    "SessionSummaryOut",
    "StoryMomentOut",
    "StreamEnvelope",
    "TrackOutlineOut",
    "VersionOut",
]
