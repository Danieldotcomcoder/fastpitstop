"""`assemble_evidence`: Phase 5.2 — the evidence retriever. Given an
`Anomaly`, gather everything relevant AT OR BEFORE its own `anomaly_time`
into one tight, typed, self-contained `EvidenceBundle` a future narration
step (5.3) can cite from. Still no LLM here — CLAUDE.md's line for this
prompt is retrieval and ranking only.

Four sources, one bundle:

- **Kernel state around the moment** — `f1_kernel.SessionFrames`, already
  loaded by the caller (the same "load, then pass data in" split every
  other kernel-adjacent entry point in this project uses): the lap/stint/
  compound/tyre-age/gap/track-status row at the anomaly's own lap
  (`LAP_CONTEXT`) and a degradation trend for the driver's current stint
  (`DEGRADATION_FIT`). Two things this deliberately does NOT do, both
  documented where they matter: undercut/crossover context is never
  re-derived as a separate item for `UNDERCUT_RESOLVED` — `Anomaly.summary`
  already carries the full crossover snapshot the detector scored against
  (decision lap, predicted/actual side, margins, confidence), so restating
  it here would just be a second copy of the same numbers; and
  braking-point drift is not surfaced as evidence at all — see
  `EvidenceKind` for why.
- **The raw kernel numbers that define the anomaly** — `Anomaly.summary`/
  `.reasons` themselves, always pulled as exactly one `ANOMALY_FACT` item
  (CLAUDE.md's own "what changed" facts) — the one item never dropped by
  budget trimming, since it is the thing everything else exists to explain.
- **Radio clips** (`f1_radio.search`, Phase 4's own search layer, reused
  rather than a second query path) — a driver's own radio channel already
  carries both directions of a pit-wall exchange (OpenF1's `team_radio`
  has no separate "engineer" feed — DATA_CONTRACTS.md), so "the driver's
  own + their engineer's" is exactly what a `driver`-filtered, blank-query
  (pure structured, no semantic ranking needed) search in the lookback
  window already returns. A clip with no transcript yet (fetched but not
  transcribed) contributes nothing citable and is skipped, matching
  `f1_radio`'s own "fetched, not yet transcribed" degrade state.
- **Race control messages** in the same window (`f1_domain.RaceControlMessage`,
  read directly from the session's own raw event stream — `SessionFrames`
  carries no raw events, only derived frames, so this is a third input a
  caller supplies alongside it).

**Walk-forward, not whole-session.** The prompt's own instruction is that
EVERYTHING pulled is "time-boxed to <= anomaly_time (never future data)" —
not just each item's own reported timestamp, but the DATA behind it. A
`KernelReport` handed to `detect_anomalies` ordinarily covers the WHOLE
session (Phase 5.1 reads `kernel_report.degradation_fits` directly, the
same way), so trusting its degradation fits verbatim here would let a
stint's LATER laps quietly inform "evidence" about an EARLIER anomaly in
that same stint. `_degradation_items` instead re-slices
`session_frames.laps` to `lap_number <= anomaly.lap_number` and refits
degradation on exactly that slice — the identical walk-forward recipe
`f1_kernel.undercut.undercut_snapshot` already uses ("slice, REFIT on the
slice" — enforced, not trusted), reusing the ORIGINAL report's own
`params`/`total_laps` so the refit can never silently disagree with the
computation the caller already trusted for detection.

Every source builder is trusted to respect the window it was given, but the
"never future" guarantee is additionally enforced ONCE, structurally, at
the end of `assemble_evidence` — the same "don't trust upstream, enforce at
the boundary" discipline `TimelineBuffer.release` uses for Delay Sync's own
spoiler-safety gate — so a bug in one builder can never leak a future fact
into a bundle.

Ranking/trimming: every `EvidenceItem` carries a `salience` used ONLY to
decide what survives the token budget (a small, deliberately generous-but-
cheap heuristic char-count, not a real tokenizer — see `_estimate_tokens`)
and a separate `confidence` (a normalized `[0, 1]` trust signal, derived
from whichever native taxonomy applies — `Reliability`, `Confidence`, or a
transcript's own float — never a new number invented on top of them) used
by 5.3 to decide how much to trust what got cited. The two are independent
questions: how RELEVANT a fact is to explaining this moment, and how much
to TRUST it once used.

`EvidenceBundle.low_evidence` is true when nothing beyond the anomaly's own
numbers survived: no measured lap context, no degradation fit, no relevant
race control, no radio. Only then is the model skipped. Anything measured
counts as support, and the narrator decides whether it actually explains
the moment -- it is told to decline when it does not, and every sentence it
writes still has to cite the items it used or the grounding validator
throws it away.

This used to count only radio and race control, treating lap context and
degradation as background. Two things made that wrong in practice. A tyre
eight laps fresher and an interval closing from 0.59 s to 0.33 s IS why one
car passed another; the product's own design reads exactly that way. And
with the radio corpus empty, the only thing clearing the bar was whatever
race-control message happened to fall in the lookback window -- usually a
blue flag for some unrelated car -- so the rule admitted noise and
excluded the facts that actually explained the moment. Race control is now
also filtered for relevance (`_race_control_items`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from f1_domain import DomainEvent, DriverListUpdated, DriverRef, RaceControlMessage
from f1_kernel import (
    Confidence,
    DegradationFit,
    KernelReport,
    Reliability,
    SessionFrames,
    clean_laps,
    fit_degradation,
    fuel_correct,
)
from f1_radio.search import SearchFilters, SearchIndex, search_transcripts
from f1_radio.transcription import DEFAULT_LOW_CONFIDENCE_THRESHOLD
from f1_why.anomaly import Anomaly, AnomalyKind, DetailValue

_RELIABILITY_CONFIDENCE: dict[Reliability, float] = {
    Reliability.GOOD: 1.0,
    Reliability.THIN: 0.6,
    Reliability.UNRELIABLE: 0.3,
}
"""Reuses `f1_kernel.degradation.Reliability` verbatim rather than inventing
a parallel taxonomy — the same discipline `f1_kernel.braking` follows by
reusing this exact enum for braking-drift fits."""

_CROSSOVER_CONFIDENCE: dict[Confidence, float] = {
    Confidence.HIGH: 1.0,
    Confidence.MEDIUM: 0.6,
    Confidence.LOW: 0.3,
}
"""Ditto for `f1_kernel.undercut.Confidence`, when an anomaly's own summary
carries one (`UNDERCUT_RESOLVED`)."""

_ANOMALY_FACT_ID = "anomaly"


class EvidenceKind(StrEnum):
    """What an `EvidenceItem` is evidence OF — narrow, and each member
    documents which `Anomaly` kinds it applies to and where it comes from.

    Deliberately NOT a member here: braking-point drift. `OFF_TRACK`'s own
    signal is a timing SECTOR (1/2/3); `f1_kernel.braking`'s drift fits are
    keyed by CIRCUIT CORNER, a different granularity this schema has no
    mapping between, and the fit itself is computed over a stint's WHOLE
    telemetry — which, for a mid-stint anomaly, routinely extends past the
    anomaly's own lap. Re-deriving it walk-forward would need a telemetry
    frame this module has no input for (opt-in and expensive per
    `f1_kernel.report`'s own docs, and citing it unsliced would violate this
    module's own "never future data" contract) — so rather than either
    silently leak or bolt on telemetry loading no prompt asked for, this
    kind is simply not built. Braking drift is also empty on almost every
    real `KernelReport` in the first place (it is opt-in, per
    `analyze_session`'s `braking_drivers=`), so the omission costs little.
    """

    ANOMALY_FACT = "anomaly_fact"
    """The detector's own numbers (`Anomaly.summary`/`.reasons`) — always
    exactly one per bundle, never dropped by trimming."""

    LAP_CONTEXT = "lap_context"
    """Compound/tyre age/gap-ahead/track-status/position at the anomaly's
    own lap, straight from the already-loaded lap frame — one per driver in
    `Anomaly.drivers` that has a matching row."""

    DEGRADATION_FIT = "degradation_fit"
    """The driver's current-stint degradation trend, refit WALK-FORWARD on
    laps up to the anomaly's own lap only — see the module docstring."""

    RACE_CONTROL = "race_control"
    """A `RaceControlMessage` in the lookback window — flags, investigations,
    safety car/VSC, penalties."""

    RADIO_CLIP = "radio_clip"
    """A transcribed team-radio clip for one of `Anomaly.drivers`, in the
    lookback window."""

    GAP_TREND = "gap_trend"
    """For an overtake: the gap between the two cars at the timing line over
    the laps leading up to it, from the lap frame's own lap end times. How a
    pass was set up (closing in over several laps, or out of nowhere)."""


_SUPPORTING_KINDS: frozenset[EvidenceKind] = frozenset(
    {
        EvidenceKind.RADIO_CLIP,
        EvidenceKind.RACE_CONTROL,
        EvidenceKind.LAP_CONTEXT,
        EvidenceKind.DEGRADATION_FIT,
        EvidenceKind.GAP_TREND,
    }
)
"""Every kind except the anomaly's own fact: what counts towards
`EvidenceBundle.low_evidence`. See the module docstring for why measured
kernel facts are in this set."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One citable fact. `evidence_id` is stable (deterministic given the
    same anomaly/sources) so a narrator can cite it and a UI can link back
    to whatever it came from."""

    evidence_id: str
    kind: EvidenceKind
    event_time: datetime
    """UTC instant this fact pertains to. Always `<=` the owning bundle's
    `Anomaly.anomaly_time` — enforced structurally in `assemble_evidence`,
    never merely assumed of a source builder."""
    drivers: tuple[str, ...]
    """Which driver(s) this item is about; empty for a track-wide fact
    (e.g. a track-scoped race-control message)."""
    summary: str
    """The one factual sentence a narrator is allowed to draw on — already
    phrased in natural language so 5.3 does not have to invent wording for
    a raw number."""
    data: dict[str, DetailValue]
    """The raw fields `summary` was built from, flat and JSON-friendly —
    the same "machine-readable, never prose" discipline `Anomaly.summary`
    itself follows."""
    confidence: float
    """Normalized `[0, 1]` trust signal — how much to believe this item's
    content, independent of how relevant it is. `1.0` for a directly
    observed/measured fact (a lap's own recorded compound, a race-control
    message); a fitted quantity's own `Reliability`/`Confidence` tier
    mapped onto the same scale otherwise."""
    confidence_label: str
    """The NATIVE vocabulary `confidence` was derived from (`"good"`,
    `"high"`, `"measured"`, `"low_confidence"`, ...) — so a narrator or UI
    can use the project's own familiar terms rather than a bare float."""
    salience: float
    """Ranking score used ONLY to decide what survives `_trim_to_budget` —
    unrelated to `confidence`. Relevance and trustworthiness are different
    questions and must not be conflated into one number."""
    source: str
    """Where this item came from, for a UI's "why am I seeing this" or a
    debugging session — e.g. `"f1_kernel.fit_degradation"`, `"f1_radio.search"`."""


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Everything a narrator is allowed to say about one `Anomaly` —
    self-contained and factual per CLAUDE.md's own line for this prompt:
    nothing outside `items` may ever be asserted."""

    anomaly: Anomaly
    anomaly_id: str
    """Stable id for this anomaly — see `anomaly_id()`. Not a field on
    `Anomaly` itself (that dataclass is Phase 5.1's own frozen contract);
    computed here so 5.3's explanation cache has a real key to cache against."""
    items: tuple[EvidenceItem, ...]
    """Already ranked and trimmed — the anomaly's own fact first, then the
    rest by descending `salience`."""
    low_evidence: bool
    """True when nothing beyond the anomaly's own fact survived. The signal
    5.3 uses to emit "no clear cause" without asking a model to invent one.
    See the module docstring for the full reasoning."""
    token_budget: int
    estimated_tokens: int
    """Sum of `_estimate_tokens` over the surviving `items` — an
    approximation (character count, not a real tokenizer), so treat it as
    order-of-magnitude, not exact."""
    notes: tuple[str, ...]
    """Anything about this bundle worth surfacing that is not itself
    evidence: no radio corpus configured, items dropped for budget."""
    roster: tuple[DriverRef, ...] = ()
    """Who the racing numbers in `items` are, for every driver the bundle
    mentions. Identity, not evidence: it lets a narrator write "Russell"
    instead of "driver 63", and is never citable. Empty when the session's
    stream carried no driver list."""


class EvidenceParams(BaseModel):
    """Every tunable constant this module uses, typed and overridable — the
    same "no hardcoded magic numbers" discipline `KernelParams`/
    `AnomalyParams` already apply. None of these are calibrated against a
    measured corpus of "how far back does a genuinely causal radio call
    usually sit" — PROVISIONAL/STRUCTURAL defaults, same honesty every other
    not-yet-calibrated parameter set in this project carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback_s: float = Field(
        default=180.0,
        gt=0,
        description=(
            "How far before `anomaly_time`, in seconds, radio clips and "
            "race-control messages are searched. Long enough to cover a "
            "short pit-wall exchange or a caution period leading into the "
            "anomaly; PROVISIONAL, not fit against a measured corpus."
        ),
    )
    max_radio_clips_per_driver: int = Field(
        default=3,
        ge=0,
        description="Cap on retrieved radio clips per driver before ranking/trimming.",
    )
    max_race_control_messages: int = Field(
        default=5,
        ge=0,
        description="Cap on retrieved race-control messages before ranking/trimming.",
    )
    token_budget: int = Field(
        default=600,
        gt=0,
        description=(
            "Approximate token ceiling for the SUPPORTING evidence (the "
            "anomaly's own fact is always kept regardless). Deliberately "
            "small: this feeds one cheap, fast narration call for one or "
            "two sentences (CLAUDE.md's Phase 5 budget invariant), not a "
            "research report."
        ),
    )
    chars_per_token: float = Field(
        default=4.0,
        gt=0,
        description=(
            "Characters-per-token heuristic used by `_estimate_tokens` — an "
            "approximation chosen to avoid a real tokenizer dependency in a "
            "package that otherwise touches no LLM at all. Order-of-"
            "magnitude only."
        ),
    )
    low_evidence_min_supporting_items: int = Field(
        default=1,
        ge=0,
        description=(
            "Minimum number of SUPPORTING items (anything but the"
            " anomaly's own fact) that must survive trimming before the"
            " narrator is asked at all. Below this, `low_evidence=True`."
        ),
    )
    gap_trend_laps: int = Field(
        default=3,
        ge=1,
        description=(
            "How many laps before an overtake its gap trend covers. Enough to "
            "show a car closing in; STRUCTURAL."
        ),
    )
    race_control_lead_s: float = Field(
        default=60.0,
        ge=0,
        description=(
            "For a moment that spans a period (a safety car, a retirement), "
            "how far before the period began race control is searched: the "
            "incident that caused it is usually reported just before or after."
        ),
    )

    def with_overrides(self, **overrides: object) -> EvidenceParams:
        """A copy with `overrides` applied, re-validated. Raises on an
        unknown parameter name or an out-of-range value, matching
        `KernelParams.with_overrides`/`AnomalyParams.with_overrides`."""
        return EvidenceParams.model_validate({**self.model_dump(), **overrides})


@dataclass(frozen=True, slots=True)
class EvidenceSources:
    """Everything `assemble_evidence` may draw from for one session —
    loaded ONCE by the caller, the "load, then pass data in" split every
    kernel-adjacent entry point in this project already follows.
    `session_frames`/`kernel_report` should ordinarily be the SAME pair
    `detect_anomalies` was called with (they describe one session; the
    degradation walk-forward refit reuses `kernel_report.params`/
    `.total_laps` so it can never silently disagree with them).
    `radio_index`/`events` are optional: a session with no configured radio
    corpus, or no raw event stream in hand, still assembles whatever the
    other sources can provide — never a hard failure, just a `notes` entry
    saying what was unavailable.
    """

    session_frames: SessionFrames
    kernel_report: KernelReport
    events: tuple[DomainEvent, ...] = ()
    """The session's own raw `DomainEvent` stream — `RaceControlMessage`
    lives only here; `SessionFrames`/`KernelReport` carry no raw events,
    only derived frames."""
    radio_index: SearchIndex | None = None
    """`None` when no radio corpus is configured for this session (Phase 4
    is 2024+ backfill-dependent, and not every session has been fetched/
    transcribed/indexed yet) — degrades to a `notes` entry, never an error."""


def anomaly_id(anomaly: Anomaly) -> str:
    """A stable id for `anomaly`, derived from its own content rather than
    a field on the frozen `Anomaly` dataclass (Phase 5.1's own contract,
    not reopened here). `detect_anomalies` is deterministic — same frames/
    report in, byte-identical anomalies out — so this is stable across runs
    over the same session, which is exactly what 5.3's explanation cache
    needs to key against."""
    drivers = "+".join(anomaly.drivers)
    lap = "na" if anomaly.lap_number is None else str(anomaly.lap_number)
    return f"{anomaly.kind.value}:{lap}:{drivers}:{int(anomaly.anomaly_time.timestamp())}"


def assemble_evidence(
    anomaly: Anomaly, sources: EvidenceSources, params: EvidenceParams
) -> EvidenceBundle:
    """Gather, rank and trim everything relevant to `anomaly`, at or before
    its own `anomaly_time`, into one self-contained bundle.

    Pure over already-loaded inputs: no network call happens here beyond
    whatever `sources.radio_index` itself does under the hood (a
    `MemorySearchIndex` does none at all; a `PostgresSearchIndex` is the
    one real I/O this function can trigger, behind the same protocol
    `f1_radio.search` already uses everywhere else). Deterministic given
    deterministic inputs.
    """
    window_start = anomaly.anomaly_time - timedelta(seconds=params.lookback_s)
    notes: list[str] = []

    candidates: list[EvidenceItem] = [_anomaly_fact_item(anomaly)]
    candidates.extend(_lap_context_items(anomaly, sources.session_frames))
    candidates.extend(_gap_trend_items(anomaly, sources.session_frames, params))
    candidates.extend(_degradation_items(anomaly, sources.session_frames, sources.kernel_report))
    candidates.extend(
        _race_control_items(
            anomaly, sources.events, _race_control_window(anomaly, window_start, params), params
        )
    )
    if sources.radio_index is not None:
        candidates.extend(_radio_items(anomaly, sources.radio_index, window_start, params))
    else:
        notes.append("no radio corpus configured for this session")

    # Structural, final enforcement of "never future" — a source builder is
    # trusted to respect its own window, but this is the one place the
    # guarantee actually holds regardless, the same "don't trust upstream,
    # enforce at the boundary" discipline Delay Sync's own release() uses.
    safe = [item for item in candidates if item.event_time <= anomaly.anomaly_time]

    items, estimated_tokens, dropped = _trim_to_budget(safe, params)
    if dropped:
        notes.append(f"{dropped} lower-salience evidence item(s) dropped to fit the token budget")

    supporting = sum(1 for item in items if item.kind in _SUPPORTING_KINDS)
    low_evidence = supporting < params.low_evidence_min_supporting_items

    mentioned = {
        *anomaly.drivers,
        *_summary_drivers(anomaly),
        *(driver for item in items for driver in item.drivers),
    }
    return EvidenceBundle(
        anomaly=anomaly,
        anomaly_id=anomaly_id(anomaly),
        items=tuple(items),
        low_evidence=low_evidence,
        token_budget=params.token_budget,
        estimated_tokens=estimated_tokens,
        notes=tuple(notes),
        roster=_roster(sources.events, anomaly.anomaly_time, mentioned),
    )


_SUMMARY_DRIVER_KEYS: tuple[str, ...] = (
    "rival",
    "passed",
    "passed_by",
    "pitted",
    "retired",
    "leader",
    "pole",
    "leader_before",
    "leader_after",
    "best_gainer",
    "winner",
    "second",
    "third",
    "gainers",
    "losers",
)
"""`Anomaly.summary` keys whose values name drivers: a single number, a
comma-joined list, or `driver:places` pairs."""


def _summary_drivers(anomaly: Anomaly) -> set[str]:
    """Every driver an anomaly's summary names, so a narrator can name them
    too (a pass of three cars names all three, not only the first)."""
    named: set[str] = set()
    for key in _SUMMARY_DRIVER_KEYS:
        value = anomaly.summary.get(key)
        if not isinstance(value, str):
            continue
        for part in value.split(","):
            driver = part.partition(":")[0].strip()
            if driver:
                named.add(driver)
    return named


def _roster(
    events: Sequence[DomainEvent], at: datetime, mentioned: set[str]
) -> tuple[DriverRef, ...]:
    """The latest driver list at or before `at`, narrowed to the drivers the
    bundle actually mentions and ordered by racing number.

    At or before, like every other input here: a driver list is identity
    rather than race data, but holding it to the same boundary costs nothing
    and keeps "nothing after the anomaly" true without exceptions."""
    latest: DriverListUpdated | None = None
    for event in events:
        if (
            isinstance(event, DriverListUpdated)
            and event.ts_utc <= at
            and (latest is None or event.ts_utc >= latest.ts_utc)
        ):
            latest = event
    if latest is None or not latest.drivers:
        return ()
    by_number = {ref.number: ref for ref in latest.drivers}
    return tuple(
        by_number[number]
        for number in sorted(mentioned, key=lambda n: (len(n), n))
        if number in by_number
    )


# ---------------------------------------------------------------------------
# the anomaly's own fact
# ---------------------------------------------------------------------------


def _anomaly_fact_item(anomaly: Anomaly) -> EvidenceItem:
    confidence, label = _anomaly_confidence(anomaly)
    summary = "; ".join(anomaly.reasons) if anomaly.reasons else f"{anomaly.kind.value} detected"
    return EvidenceItem(
        evidence_id=_ANOMALY_FACT_ID,
        kind=EvidenceKind.ANOMALY_FACT,
        event_time=anomaly.anomaly_time,
        drivers=anomaly.drivers,
        summary=summary,
        data=dict(anomaly.summary),
        confidence=confidence,
        confidence_label=label,
        salience=1.0,
        source="f1_why.detect_anomalies",
    )


def _anomaly_confidence(anomaly: Anomaly) -> tuple[float, str]:
    """Most anomaly kinds are a directly measured outlier — `"measured"`,
    trusted at face value. The two kinds whose own `summary` already carries
    a fitted-quality signal reuse THAT signal rather than asserting
    certainty on top of it."""
    if anomaly.kind is AnomalyKind.DEG_CLIFF:
        raw = anomaly.summary.get("fit_reliability")
        if isinstance(raw, str):
            try:
                reliability = Reliability(raw)
            except ValueError:
                pass
            else:
                return _RELIABILITY_CONFIDENCE[reliability], reliability.value
    if anomaly.kind is AnomalyKind.UNDERCUT_RESOLVED:
        raw = anomaly.summary.get("confidence")
        if isinstance(raw, str):
            try:
                confidence = Confidence(raw)
            except ValueError:
                pass
            else:
                return _CROSSOVER_CONFIDENCE[confidence], confidence.value
    return 1.0, "measured"


# ---------------------------------------------------------------------------
# kernel state: lap context + walk-forward degradation
# ---------------------------------------------------------------------------


def _lap_row(laps: pl.DataFrame, driver: str, lap_number: int) -> dict[str, object] | None:
    rows = laps.filter((pl.col("driver") == driver) & (pl.col("lap_number") == lap_number))
    if rows.is_empty():
        return None
    return rows.row(0, named=True)


def _lap_context_items(anomaly: Anomaly, frames: SessionFrames) -> list[EvidenceItem]:
    if anomaly.lap_number is None:
        return []
    items: list[EvidenceItem] = []
    for driver in anomaly.drivers:
        row = _lap_row(frames.laps, driver, anomaly.lap_number)
        if row is None:
            continue
        end = row.get("lap_end_utc")
        if not isinstance(end, datetime):
            continue
        # A two-driver anomaly's `anomaly_time` is the LATER of the pair's
        # own lap ends, so the OTHER driver's own row can sit a few seconds
        # after it. Static facts (compound/tyre age/position) are still
        # fair context either way; only a WINDOW-AGGREGATED value
        # (gap_ahead_s, summarised over the whole lap) is dropped when the
        # lap itself hadn't genuinely finished by anomaly_time — the one
        # value here that could otherwise reflect a sliver of future data.
        was_clamped = end > anomaly.anomaly_time
        event_time = min(end, anomaly.anomaly_time)

        compound = row.get("compound")
        tyre_age = row.get("tyre_age")
        gap_ahead = row.get("gap_ahead_s")
        status = row.get("track_status")
        position = row.get("position")

        safe_gap_ahead = None
        if isinstance(gap_ahead, int | float) and not was_clamped:
            safe_gap_ahead = gap_ahead

        parts = [f"driver {driver} on lap {anomaly.lap_number}"]
        if isinstance(compound, str):
            age_part = f", tyre age {tyre_age}" if tyre_age is not None else ""
            parts.append(f"was on {compound}{age_part}")
        if safe_gap_ahead is not None:
            parts.append(f"gap to the car ahead was {safe_gap_ahead:.1f}s")
        if isinstance(status, str) and status != "green":
            parts.append(f"track status was {status}")
        summary = ", ".join(parts) + "."

        items.append(
            EvidenceItem(
                evidence_id=f"lap_context:{driver}",
                kind=EvidenceKind.LAP_CONTEXT,
                event_time=event_time,
                drivers=(driver,),
                summary=summary,
                data={
                    "compound": compound if isinstance(compound, str) else None,
                    "tyre_age": tyre_age if isinstance(tyre_age, int) else None,
                    "gap_ahead_s": safe_gap_ahead,
                    "track_status": status if isinstance(status, str) else None,
                    "position": position if isinstance(position, int) else None,
                },
                confidence=1.0,
                confidence_label="measured",
                salience=0.5,
                source="f1_kernel.lap_frame",
            )
        )
    return items


def _gap_trend_items(
    anomaly: Anomaly, frames: SessionFrames, params: EvidenceParams
) -> list[EvidenceItem]:
    """An overtake's run-up: the passing car's gap to the car it passed at
    the timing line, for each of the `gap_trend_laps` laps before the pass
    (the difference of their own lap end times on the same lap). Only laps
    both cars completed before the pass count, so nothing here is later
    than the moment."""
    if (
        anomaly.kind is not AnomalyKind.POSITION_CHANGE
        or anomaly.summary.get("gained") is not True
        or len(anomaly.drivers) < 2
        or anomaly.lap_number is None
    ):
        return []
    passer, rival = anomaly.drivers[0], anomaly.drivers[1]
    first = max(1, anomaly.lap_number - params.gap_trend_laps)
    rows = frames.laps.filter(
        pl.col("driver").is_in([passer, rival])
        & (pl.col("lap_number") >= first)
        & (pl.col("lap_number") < anomaly.lap_number)
    ).select("driver", "lap_number", "lap_end_utc")
    ends: dict[tuple[str, int], datetime] = {
        (str(row["driver"]), int(row["lap_number"])): row["lap_end_utc"]
        for row in rows.to_dicts()
        if isinstance(row["lap_end_utc"], datetime)
    }
    gaps: list[tuple[int, float]] = []
    for lap_number in range(first, anomaly.lap_number):
        passer_end, rival_end = ends.get((passer, lap_number)), ends.get((rival, lap_number))
        if passer_end is None or rival_end is None:
            continue
        gaps.append((lap_number, (passer_end - rival_end).total_seconds()))
    if not gaps:
        return []
    last_end = max(ends[(passer, gaps[-1][0])], ends[(rival, gaps[-1][0])])
    if last_end > anomaly.anomaly_time:
        return []
    described = ", ".join(f"{gap:.1f}s after lap {lap}" for lap, gap in gaps)
    data: dict[str, DetailValue] = {"from_lap": gaps[0][0], "to_lap": gaps[-1][0]}
    data.update({f"gap_lap_{lap}_s": round(gap, 3) for lap, gap in gaps})
    data["first_gap_s"] = round(gaps[0][1], 3)
    data["last_gap_s"] = round(gaps[-1][1], 3)
    return [
        EvidenceItem(
            evidence_id=f"gap_trend:{passer}:{rival}",
            kind=EvidenceKind.GAP_TREND,
            event_time=last_end,
            drivers=(passer, rival),
            summary=f"driver {passer} ran behind driver {rival} by {described}.",
            data=data,
            confidence=1.0,
            confidence_label="measured",
            salience=0.55,
            source="f1_kernel.lap_frame",
        )
    ]


def _fit_for(
    fits: Sequence[DegradationFit], driver: str, stint_number: int
) -> DegradationFit | None:
    matching = [fit for fit in fits if fit.driver == driver and fit.stint_number == stint_number]
    if not matching:
        return None
    return max(matching, key=lambda fit: fit.n_laps_used)


def _degradation_items(
    anomaly: Anomaly, frames: SessionFrames, report: KernelReport
) -> list[EvidenceItem]:
    """Walk-forward: refit degradation on laps up to `anomaly.lap_number`
    ONLY, the same "slice, then refit on the slice" recipe
    `f1_kernel.undercut.undercut_snapshot` uses, rather than trusting
    `report.degradation_fits` verbatim — that report ordinarily covers the
    WHOLE session, and a stint's later laps must never inform "evidence"
    about an earlier anomaly in that same stint."""
    if anomaly.lap_number is None:
        return []
    sliced = frames.laps.filter(pl.col("lap_number") <= anomaly.lap_number)
    if sliced.is_empty():
        return []
    cleaned = clean_laps(sliced, report.params)
    corrected = fuel_correct(cleaned, report.params, total_laps=report.total_laps)
    fits = fit_degradation(corrected, report.params)
    if not fits:
        return []

    items: list[EvidenceItem] = []
    for driver in anomaly.drivers:
        row = _lap_row(sliced, driver, anomaly.lap_number)
        stint_number = row.get("stint_number") if row is not None else None
        if not isinstance(stint_number, int):
            continue
        fit = _fit_for(fits, driver, stint_number)
        if fit is None:
            continue
        summary = (
            f"driver {driver}'s stint {fit.stint_number} ({fit.compound}) fuel-corrected pace "
            f"was trending {fit.deg_rate_s_per_lap:+.3f}s/lap over the {fit.n_laps_used} clean "
            f"laps up to this point ({fit.reliability.value} fit)."
        )
        items.append(
            EvidenceItem(
                evidence_id=f"degradation_fit:{driver}:{fit.stint_number}",
                kind=EvidenceKind.DEGRADATION_FIT,
                event_time=anomaly.anomaly_time,
                drivers=(driver,),
                summary=summary,
                data={
                    "stint_number": fit.stint_number,
                    "compound": fit.compound,
                    "deg_rate_s_per_lap": fit.deg_rate_s_per_lap,
                    "n_laps_used": fit.n_laps_used,
                    "reliability": fit.reliability.value,
                },
                confidence=_RELIABILITY_CONFIDENCE[fit.reliability],
                confidence_label=fit.reliability.value,
                salience=0.45,
                source="f1_kernel.fit_degradation (walk-forward)",
            )
        )
    return items


# ---------------------------------------------------------------------------
# race control
# ---------------------------------------------------------------------------


_TRACK_WIDE_KINDS: frozenset[AnomalyKind] = frozenset(
    {AnomalyKind.NEUTRALIZATION, AnomalyKind.RACE_START}
)
"""Moments about the whole field: any car race control names may be part of
the story (the incident that caused a safety car), not only `drivers`."""

_NAMED_CAR = re.compile(r"\b(\d{1,2}) \([A-Z]{3}\)")
"""How race control names a car in its text: `CAR 20 (MAG)`, `CARS 20 (MAG)
AND 2 (SAR)`. Incident messages carry no driver field of their own."""

_RACE_CONTROL_NOISE: tuple[str, ...] = (
    "DRS ENABLED",
    "DRS DISABLED",
    "CLEAR IN TRACK SECTOR",
    "TRACK CLEAR",
    "AWNINGS",
    "PUSH CARS",
    "RISK OF RAIN",
    "BLUE FLAG",
)
"""Message text that never explains a moment by itself. Blue flags are here
too: a car being lapped is real, but it is the lap frame, not a flag, that
says it lost time."""


def _named_cars(message: RaceControlMessage) -> tuple[str, ...]:
    if message.driver is not None:
        return (message.driver,)
    text = message.message or ""
    named: list[str] = []
    for number in _NAMED_CAR.findall(text):
        if number not in named:
            named.append(number)
    return tuple(named)


def _race_control_window(
    anomaly: Anomaly, default_start: datetime, params: EvidenceParams
) -> datetime:
    """A moment that spans a period carries its start as `window_start_utc`
    (a safety car from its first lap, a retirement from the car's last lap);
    race control is searched from just before then, not only the last
    `lookback_s` before the moment was reported."""
    raw = anomaly.summary.get("window_start_utc")
    if not isinstance(raw, str):
        return default_start
    try:
        began = datetime.fromisoformat(raw)
    except ValueError:
        return default_start
    return min(default_start, began - timedelta(seconds=params.race_control_lead_s))


def _race_control_items(
    anomaly: Anomaly,
    events: Sequence[DomainEvent],
    window_start: datetime,
    params: EvidenceParams,
) -> list[EvidenceItem]:
    # Relevant means it names one of this anomaly's drivers or names nobody
    # (a safety car, a red flag, a sector yellow). A message about some
    # other car is not evidence about this moment: in the 2025 Spanish GP
    # the five most recent messages before a Russell/Antonelli undercut
    # verdict were blue flags for three uninvolved backmarkers, and they
    # were the whole race-control contribution to its bundle. Cars named
    # only in the text count as named. A track-wide moment keeps every car
    # named and reads EARLIEST first: what caused a safety car is reported
    # as it starts, while the last messages before the restart are about
    # the restart.
    involved = set(anomaly.drivers)
    track_wide = anomaly.kind in _TRACK_WIDE_KINDS
    relevant: list[RaceControlMessage] = []
    for event in events:
        if not isinstance(event, RaceControlMessage):
            continue
        if not window_start <= event.ts_utc <= anomaly.anomaly_time:
            continue
        text = (event.message or "").upper()
        if any(noise in text for noise in _RACE_CONTROL_NOISE):
            continue
        if track_wide and "TRACK LIMITS" in text:
            continue  # one car's deleted lap time, never what neutralized a race
        named = _named_cars(event)
        if track_wide or not named or involved.intersection(named):
            relevant.append(event)

    # Incidents, penalties and stopped cars first, whatever the moment: in
    # the 2024 Austrian GP the note of the Norris/Verstappen collision was
    # pushed out of Norris's retirement by five newer messages naming
    # nobody (pit lane closed, a VSC deployed and ended). Within each group,
    # a track-wide moment reads earliest first (what started it), any other
    # most recent first (what led into it).
    def substantive(event: RaceControlMessage) -> bool:
        text = (event.message or "").upper()
        return any(word in text for word in ("INCIDENT", "PENALTY", "STOPPED"))

    by_time = sorted(relevant, key=lambda event: event.ts_utc, reverse=not track_wide)
    messages = [e for e in by_time if substantive(e)] + [e for e in by_time if not substantive(e)]
    messages = messages[: params.max_race_control_messages]

    items: list[EvidenceItem] = []
    for message in messages:
        parts = [
            part
            for part in (message.category, message.flag, message.scope, message.message)
            if part
        ]
        summary = " — ".join(parts) if parts else "race control message"
        drivers = _named_cars(message)
        items.append(
            EvidenceItem(
                evidence_id=f"race_control:{message.ts_utc.isoformat()}:{message.source_seq}",
                kind=EvidenceKind.RACE_CONTROL,
                event_time=message.ts_utc,
                drivers=drivers,
                summary=summary,
                data={
                    "category": message.category,
                    "flag": message.flag,
                    "scope": message.scope,
                    "message": message.message,
                    "driver": message.driver,
                },
                confidence=1.0,
                confidence_label="official",
                salience=_recency_salience(0.85, message.ts_utc, anomaly.anomaly_time, params),
                source="race_control_message",
            )
        )
    return items


# ---------------------------------------------------------------------------
# radio
# ---------------------------------------------------------------------------


def _radio_items(
    anomaly: Anomaly,
    index: SearchIndex,
    window_start: datetime,
    params: EvidenceParams,
) -> list[EvidenceItem]:
    """A `driver`-scoped, blank-query (pure structured filter, no semantic
    ranking — there is no topic to search for here) call per driver in
    `anomaly.drivers`, reusing `f1_radio.search.search_transcripts` rather
    than a second query path onto the same corpus."""
    items: list[EvidenceItem] = []
    for driver in anomaly.drivers:
        filters = SearchFilters(
            driver=driver, captured_after=window_start, captured_before=anomaly.anomaly_time
        )
        results = search_transcripts(
            "",
            index=index,
            embedding_backend=None,
            filters=filters,
            limit=params.max_radio_clips_per_driver,
        )
        for result in results:
            record = result.record
            if not record.transcript_text or record.captured_at is None:
                continue  # fetched but not yet transcribed — nothing citable
            confidence = record.confidence if record.confidence is not None else 0.5
            label = "transcribed"
            if confidence < DEFAULT_LOW_CONFIDENCE_THRESHOLD:
                label = "low_confidence"
            items.append(
                EvidenceItem(
                    evidence_id=f"radio_clip:{driver}:{record.captured_at.isoformat()}",
                    kind=EvidenceKind.RADIO_CLIP,
                    event_time=record.captured_at,
                    drivers=(driver,),
                    summary=f'driver {driver} radio: "{record.transcript_text.strip()}"',
                    data={
                        "audio_path": record.audio_path,
                        "language": record.language,
                        "lap_number": record.lap_number,
                        "position": record.position,
                        "track_status": record.track_status,
                    },
                    confidence=confidence,
                    confidence_label=label,
                    salience=_recency_salience(
                        0.9, record.captured_at, anomaly.anomaly_time, params
                    ),
                    source="f1_radio.search",
                )
            )
    return items


# ---------------------------------------------------------------------------
# ranking + trimming
# ---------------------------------------------------------------------------


def _recency_salience(
    base_weight: float, event_time: datetime, anomaly_time: datetime, params: EvidenceParams
) -> float:
    """`base_weight` (how relevant this KIND of evidence typically is)
    modulated by how close `event_time` sits to `anomaly_time` within the
    lookback window — never fully zeroed out, since even the oldest item in
    the window is still inside it by construction."""
    age_s = max(0.0, (anomaly_time - event_time).total_seconds())
    recency = 1.0 - min(age_s / params.lookback_s, 1.0) if params.lookback_s > 0 else 1.0
    return base_weight * (0.5 + 0.5 * recency)


def _estimate_tokens(text: str, params: EvidenceParams) -> int:
    """A deliberately approximate heuristic (character count / a fixed
    ratio), not a real tokenizer — see `EvidenceParams.chars_per_token`."""
    return max(1, math.ceil(len(text) / params.chars_per_token))


def _trim_to_budget(
    candidates: Sequence[EvidenceItem], params: EvidenceParams
) -> tuple[list[EvidenceItem], int, int]:
    """Greedy fill by descending `salience`: the anomaly's own fact is
    always kept (its cost still counts towards the total); every other
    item is kept if it still fits within the remaining budget, otherwise
    skipped (not a hard stop) — so one oversized high-salience item cannot
    starve every smaller one behind it. The kept set is returned re-sorted
    with the anomaly fact first, then by descending salience, so a caller
    never has to re-derive presentation order."""
    ordered = sorted(
        candidates,
        key=lambda item: (-item.salience, item.kind.value, item.evidence_id),
    )

    kept: list[EvidenceItem] = []
    total = 0
    dropped = 0
    for item in ordered:
        cost = _estimate_tokens(item.summary, params)
        if item.kind is EvidenceKind.ANOMALY_FACT or total + cost <= params.token_budget:
            kept.append(item)
            total += cost
        else:
            dropped += 1

    kept.sort(
        key=lambda item: (
            item.kind is not EvidenceKind.ANOMALY_FACT,
            -item.salience,
            item.evidence_id,
        )
    )
    return kept, total, dropped


__all__ = [
    "EvidenceBundle",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceParams",
    "EvidenceSources",
    "anomaly_id",
    "assemble_evidence",
]
