"""Kernel dataclass/DataFrame -> pydantic response model. A pure reshape,
nothing else: field names and values pass straight through
(`dataclasses.asdict` for the kernel's frozen dataclasses, `DataFrame.
to_dicts()` for its polars frames), then `Model.model_validate`. No
analytic runs here — that would violate CLAUDE.md's Phase 3 invariant that
the API is a thin read model over Phases 1-2, never a second home for
kernel logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

import polars as pl

from f1_api.radio import clip_id_path
from f1_api.schemas import (
    AnomalyOut,
    BrakingDriftOut,
    BrakingPointRowOut,
    CircuitOut,
    CitedEvidenceOut,
    ClassificationEntryOut,
    CrossoverResultOut,
    DegradationByCompoundRowOut,
    DegradationFitOut,
    ExplanationOut,
    KernelReportOut,
    LapRowOut,
    LapSummaryRowOut,
    RadioClipOut,
    RadioSearchResultOut,
    RadioSegmentOut,
    SessionDetailOut,
    SessionSummaryOut,
)
from f1_api.sessions import SessionEntry, session_key_path
from f1_api.why import anomaly_id_path, evidence_href
from f1_domain import DomainEvent, DriverListUpdated, SessionKey, SessionStatusChanged
from f1_kernel import BrakingDrift, Circuit, CrossoverResult, DegradationFit, KernelReport
from f1_kernel.circuit import circuit_for_session
from f1_kernel.status import track_status_timeline
from f1_radio.search import SearchResult
from f1_radio.store import TranscriptRecord
from f1_why import Anomaly, EvidenceBundle, Explanation

if TYPE_CHECKING:  # geometry imports the stream, which imports this module
    from f1_api.geometry import SessionGeometry


def session_summary(entry: SessionEntry) -> SessionSummaryOut:
    """`GET /sessions` row — metadata + data badges, no frame load."""
    circuit = circuit_for_session(entry.event_name, entry.circuit_name or entry.location)
    tables = {name.casefold() for name in entry.tables}
    has_radio = any("radio" in name for name in tables)
    has_telemetry = any(
        token in name for name in tables for token in ("telemetry", "car_position", "positions")
    )
    return SessionSummaryOut(
        key=session_key_path(entry.session_key),
        session_key=entry.session_key,
        event_name=entry.event_name,
        provenance=entry.provenance,
        circuit_name=circuit.name if circuit is not None else None,
        winner=None,
        has_radio=has_radio,
        has_telemetry=has_telemetry,
        live_recorded=entry.provenance == "live",
        live=entry.live,
    )


def lap_rows(annotated_laps: pl.DataFrame) -> list[LapRowOut]:
    """`GET /sessions/{key}/laps` — `annotate_laps`' output, row for row."""
    return [LapRowOut.model_validate(row) for row in annotated_laps.to_dicts()]


def degradation_fit_rows(fits: Sequence[DegradationFit]) -> list[DegradationFitOut]:
    return [DegradationFitOut.model_validate(asdict(fit)) for fit in fits]


def crossover_rows(crossovers: Sequence[CrossoverResult]) -> list[CrossoverResultOut]:
    return [CrossoverResultOut.model_validate(asdict(c)) for c in crossovers]


def braking_drift_rows(fits: Sequence[BrakingDrift]) -> list[BrakingDriftOut]:
    return [BrakingDriftOut.model_validate(asdict(fit)) for fit in fits]


def braking_point_rows(frame: pl.DataFrame) -> list[BrakingPointRowOut]:
    """`GET /sessions/{key}/telemetry/{driver}` — `braking_points`' output."""
    return [BrakingPointRowOut.model_validate(row) for row in frame.to_dicts()]


def lap_summary_rows(frame: pl.DataFrame) -> list[LapSummaryRowOut]:
    return [LapSummaryRowOut.model_validate(row) for row in frame.to_dicts()]


def degradation_by_compound_rows(frame: pl.DataFrame) -> list[DegradationByCompoundRowOut]:
    return [DegradationByCompoundRowOut.model_validate(row) for row in frame.to_dicts()]


def classification_from_laps(laps: pl.DataFrame) -> list[ClassificationEntryOut]:
    """One row per driver, taken at their OWN last completed lap — the final
    classification for a finished historical session, the current running
    order for a live one. Derived the same way regardless of source, so the
    two never disagree about what "classification" means.

    `in_pit` is the best signal completed-lap data can give: whether that
    last lap was itself a pit-in lap. It is not a true real-time pit status
    (there is no "currently in the pit lane" fact on a lap-frame row) — see
    `f1_kernel`'s own `is_pit_in` docs.
    """
    if laps.is_empty():
        return []
    last = laps.sort(["driver", "lap_number"]).group_by("driver", maintain_order=True).last()
    best = laps.group_by("driver").agg(pl.col("lap_time_s").min().alias("best_lap_s"))
    joined = last.join(best, on="driver", how="left")
    entries = [
        ClassificationEntryOut(
            driver=row["driver"],
            position=row["position"],
            last_lap_s=row["lap_time_s"],
            best_lap_s=row["best_lap_s"],
            gap_to_leader_s=row["gap_to_leader_s"],
            compound=row["compound"],
            tyre_age=row["tyre_age"],
            in_pit=row["is_pit_in"],
            laps_completed=row["lap_number"],
        )
        for row in joined.to_dicts()
    ]
    return sorted(
        entries,
        key=lambda e: (e.position is None, e.position if e.position is not None else 0, e.driver),
    )


def circuit_out(circuit: Circuit, geometry: SessionGeometry | None = None) -> CircuitOut:
    """`GET /sessions/{key}/circuit` — `f1_kernel.Circuit`'s geometry,
    reshaped field-for-field. `outline` passes through `None` untouched
    (`f1_kernel.circuit_for_event`'s own "not every venue is present" and
    `Circuit.outline`'s own "not every circuit has run the outline step
    yet" both degrade the same way: a null field, never a guess).

    With `geometry`, its lines replace the circuit file's: the session's own
    when the file's did not fit its measured positions (`f1_api.geometry`)."""
    document = circuit.model_dump()
    if geometry is not None:
        document["outline"] = geometry.outline.model_dump() if geometry.outline else None
        document["pit_lane"] = geometry.pit_lane.model_dump() if geometry.pit_lane else None
        document["geometry_from_session"] = geometry.from_session
    return CircuitOut.model_validate(document)


def session_detail(
    entry: SessionEntry,
    laps: pl.DataFrame,
    status_events: Sequence[DomainEvent],
) -> SessionDetailOut:
    """`GET /sessions/{key}`. `status`/`track_status` come from whatever the
    session's own event stream actually says (`session_status_changed` /
    `f1_kernel.track_status_timeline`) rather than being guessed — a
    historical session that never emitted `SessionStatusChanged` (Phase 1's
    historical adapter genuinely cannot provide it, per DATA_CONTRACTS.md)
    reports `status=None`, not a fabricated "completed"."""
    status = next(
        (
            event.status
            for event in reversed(status_events)
            if isinstance(event, SessionStatusChanged)
        ),
        None,
    )
    timeline = track_status_timeline(status_events)
    track_status = timeline[-1].status.value if timeline else None
    max_lap = laps["lap_number"].max() if not laps.is_empty() else None
    current_lap = int(max_lap) if isinstance(max_lap, int | float) else None
    identities = {
        ref.number: ref
        for event in status_events
        if isinstance(event, DriverListUpdated)
        for ref in (event.drivers or [])
    }
    classification = classification_from_laps(laps)
    for row in classification:
        if (identity := identities.get(row.driver)) is not None:
            row.full_name = identity.full_name
            row.code = identity.code
            row.team = identity.team
    return SessionDetailOut(
        key=session_key_path(entry.session_key),
        session_key=entry.session_key,
        event_name=entry.event_name,
        provenance=entry.provenance,
        status=status,
        current_lap=current_lap,
        track_status=track_status,
        classification=classification,
    )


def kernel_report_out(report: KernelReport, entry: SessionEntry) -> KernelReportOut:
    """`GET /sessions/{key}/analysis` — every `KernelReport` field except
    the per-lap frames `GET .../laps` already serves (see `KernelReportOut`'s
    own docstring for why)."""
    return KernelReportOut(
        session_key=report.session_key,
        event_name=report.event_name,
        provenance=entry.provenance,
        total_laps=report.total_laps,
        decision_lap=report.decision_lap,
        lap_summary=lap_summary_rows(report.lap_summary),
        degradation_fits=degradation_fit_rows(report.degradation_fits),
        degradation_by_compound=degradation_by_compound_rows(report.degradation_by_compound),
        crossovers=crossover_rows(report.crossovers),
        braking_drift=braking_drift_rows(report.braking_drift),
        braking_drivers=list(report.braking_drivers),
        notes=list(report.notes),
        summary=report.summary(),
    )


def radio_clip_out(
    session_key: SessionKey,
    driver: str | None,
    captured_at: datetime | None,
    audio_url: str | None,
    transcript: TranscriptRecord | None,
) -> RadioClipOut:
    """Joins one clip's identity (`session_key`/`driver`/`captured_at`, from
    the originating `TeamRadioClip` event) plus its playable `audio_url`
    (also event-sourced — `TranscriptRecord` itself only ever carries a
    local cache `audio_path`, per `f1_radio.store`'s own docs) to whatever
    `radio_transcripts` row exists for it. `transcript=None` degrades every
    transcript-derived field to `None` and `has_transcript=False` — a clip
    fetched but not yet transcribed, the normal Phase 4.2 pipeline state,
    never an error."""
    clip_id = clip_id_path(session_key, driver, captured_at)
    if transcript is None:
        return RadioClipOut(
            session_key=session_key,
            driver=driver,
            captured_at=captured_at,
            clip_id=clip_id,
            audio_url=audio_url,
            has_transcript=False,
            transcript_text=None,
            language=None,
            confidence=None,
            model_version=None,
            segments=None,
            lap_number=None,
            position=None,
            track_status=None,
            track_status_flags=None,
        )
    return RadioClipOut(
        session_key=session_key,
        driver=driver,
        captured_at=captured_at,
        clip_id=clip_id,
        audio_url=audio_url,
        has_transcript=True,
        transcript_text=transcript.transcript_text,
        language=transcript.language,
        confidence=transcript.confidence,
        model_version=transcript.model_version,
        segments=(
            [RadioSegmentOut.model_validate(segment) for segment in transcript.segments]
            if transcript.segments is not None
            else None
        ),
        lap_number=transcript.lap_number,
        position=transcript.position,
        track_status=transcript.track_status,
        track_status_flags=transcript.track_status_flags,
    )


def radio_search_result_out(result: SearchResult) -> RadioSearchResultOut:
    """`GET /radio/search` — see `RadioSearchResultOut`'s own docstring for
    why `audio_url` is always `None` here."""
    record = result.record
    clip = radio_clip_out(record.session_key, record.driver, record.captured_at, None, record)
    return RadioSearchResultOut(clip=clip, similarity=result.similarity)


def anomaly_out(session_key: SessionKey, anomaly: Anomaly) -> AnomalyOut:
    """`GET /sessions/{key}/anomalies` — `f1_why.Anomaly` field-for-field."""
    return AnomalyOut(
        anomaly_id=anomaly_id_path(session_key, anomaly),
        kind=anomaly.kind.value,
        lap_number=anomaly.lap_number,
        anomaly_time=anomaly.anomaly_time,
        drivers=list(anomaly.drivers),
        magnitude=anomaly.magnitude,
        salience=anomaly.salience,
        summary=dict(anomaly.summary),
        reasons=list(anomaly.reasons),
    )


def cited_evidence_out(
    session_key: SessionKey, bundle: EvidenceBundle, explanation: Explanation
) -> list[CitedEvidenceOut]:
    """The evidence items `explanation` actually cited, with lazy-fetch hrefs."""
    cited = set(explanation.cited_evidence_ids)
    return [
        CitedEvidenceOut(
            evidence_id=item.evidence_id,
            kind=item.kind.value,
            event_time=item.event_time,
            drivers=list(item.drivers),
            summary=item.summary,
            confidence=item.confidence,
            confidence_label=item.confidence_label,
            href=evidence_href(session_key, item),
        )
        for item in bundle.items
        if item.evidence_id in cited
    ]


def explanation_out(
    session_key: SessionKey,
    anomaly: Anomaly,
    explanation: Explanation,
    bundle: EvidenceBundle,
) -> ExplanationOut:
    """`GET /anomalies/{id}/explanation` — grounded text + cited evidence."""
    return ExplanationOut(
        anomaly_id=anomaly_id_path(session_key, anomaly),
        session_key=session_key,
        kind=anomaly.kind.value,
        lap_number=anomaly.lap_number,
        drivers=list(anomaly.drivers),
        anomaly_time=anomaly.anomaly_time,
        text=explanation.text,
        confidence=explanation.confidence.value,
        grounded=explanation.grounded,
        fallback_reason=explanation.fallback_reason,
        cited_evidence=cited_evidence_out(session_key, bundle, explanation),
    )


__all__ = [
    "anomaly_out",
    "braking_drift_rows",
    "braking_point_rows",
    "cited_evidence_out",
    "classification_from_laps",
    "crossover_rows",
    "degradation_by_compound_rows",
    "degradation_fit_rows",
    "explanation_out",
    "kernel_report_out",
    "lap_rows",
    "lap_summary_rows",
    "radio_clip_out",
    "radio_search_result_out",
    "session_detail",
]
