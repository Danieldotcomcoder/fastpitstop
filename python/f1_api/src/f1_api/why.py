"""The seam for Phase 5.4: resolving the why-engine (`f1_why`) the same way
`f1_api.radio` resolves the team-radio corpus.

`get_narration_model` / `get_explanation_store` are real FastAPI `Depends`
providers — the injection seam tests need to swap in a `MockNarrationModel`
and a fresh `MemoryExplanationStore`, so no test ever needs a live API key
(CLAUDE.md's Phase 5 invariant). Production resolves to a process-level
`ClaudeHaikuModel` + `MemoryExplanationStore` singleton; a persistent
store is a later prompt's job, mirroring `f1_radio.store`'s own
Memory-now / Postgres-later split.

Orchestration helpers (`detect_session_anomalies`, `explain_session_anomaly`,
`precompute_session_explanations`) keep every route and the WebSocket
replay path from re-deriving the analyze → detect → assemble → explain
wiring by hand — the same "one place owns the chain" discipline
`f1_kernel.analyze_session` already sets for the kernel itself.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends

from f1_api.config import WHY_FIXTURE_ENV_VAR, Settings
from f1_api.radio import clip_id_path, radio_search_index_cm
from f1_api.sessions import parse_session_key, session_key_path
from f1_api.why_fixture import CitingNarrationModel
from f1_domain import DomainEvent, SessionKey
from f1_kernel import EventSource, KernelParams, KernelReport, analyze_session
from f1_radio.search import SearchIndex
from f1_why import (
    Anomaly,
    AnomalyParams,
    ClaudeHaikuModel,
    EvidenceBundle,
    EvidenceItem,
    EvidenceKind,
    EvidenceParams,
    EvidenceSources,
    Explanation,
    ExplanationStore,
    MemoryExplanationStore,
    MockNarrationModel,
    ModelNarration,
    NarrationModel,
    NarrationParams,
    assemble_evidence,
    detect_anomalies,
    explain_cached,
)
from f1_why import (
    anomaly_id as why_anomaly_id,
)

_narration_model: NarrationModel | None = None
_narration_model_lock = Lock()
_explanation_store: MemoryExplanationStore | None = None
_explanation_store_lock = Lock()


def anomaly_id_path(session_key: SessionKey, anomaly: Anomaly) -> str:
    """The `{id}` a client puts in `/anomalies/{id}/explanation` —
    round-trips through `parse_anomaly_id`. Session + `f1_why.anomaly_id`
    are each percent-encoded then joined with `~` (the same opaque-segment
    discipline `f1_api.radio.clip_id_path` already uses), because the why-
    engine's own id alone does not name a session and contains characters
    (`:`, `+`) that would otherwise collide with a hand-picked delimiter."""
    session_part = quote(session_key_path(session_key), safe="")
    why_part = quote(why_anomaly_id(anomaly), safe="")
    return f"{session_part}~{why_part}"


def parse_anomaly_id(anomaly_path_id: str) -> tuple[SessionKey, str]:
    """Inverse of `anomaly_id_path`. Raises `ValueError` on anything
    malformed — the route turns that into a 422, never a 500."""
    parts = anomaly_path_id.split("~")
    if len(parts) != 2:
        raise ValueError(
            f"malformed anomaly id {anomaly_path_id!r}, expected '<session>~<why_anomaly_id>'"
        )
    session_part, why_part = parts
    return parse_session_key(unquote(session_part)), unquote(why_part)


def evidence_href(session_key: SessionKey, item: EvidenceItem) -> str | None:
    """A lazy-fetch link for one evidence item, or `None` when no dedicated
    REST route exists for that kind (anomaly facts / race-control messages
    ride only the event stream). Radio clips resolve to `/radio/{clip_id}`;
    lap context and degradation fits point at the existing session-scoped
    kernel reads — the client fetches full detail on demand, the same
    telemetry/radio lesson Phase 3/4 already taught."""
    key = session_key_path(session_key)
    if item.kind is EvidenceKind.RADIO_CLIP:
        rest = item.evidence_id.removeprefix("radio_clip:")
        driver, sep, captured_s = rest.partition(":")
        if not sep or not captured_s:
            return None
        try:
            captured_at = datetime.fromisoformat(captured_s)
        except ValueError:
            return None
        return f"/radio/{clip_id_path(session_key, driver, captured_at)}"
    if item.kind is EvidenceKind.DEGRADATION_FIT:
        return f"/sessions/{key}/degradation"
    if item.kind is EvidenceKind.LAP_CONTEXT:
        return f"/sessions/{key}/laps"
    return None


@dataclass(frozen=True, slots=True)
class CitedEvidenceRef:
    """Lightweight citation for a WS envelope — id + kind + optional href.
    Full evidence detail lives on `GET /anomalies/{id}/explanation`."""

    evidence_id: str
    kind: str
    href: str | None


def cited_evidence_refs(
    session_key: SessionKey, bundle: EvidenceBundle, explanation: Explanation
) -> tuple[CitedEvidenceRef, ...]:
    """The citations `explanation` actually used, reshaped for the wire."""
    cited = set(explanation.cited_evidence_ids)
    return tuple(
        CitedEvidenceRef(
            evidence_id=item.evidence_id,
            kind=item.kind.value,
            href=evidence_href(session_key, item),
        )
        for item in bundle.items
        if item.evidence_id in cited
    )


def get_narration_model() -> NarrationModel:
    """Process-level lazy singleton. Resolution order:

    1. `F1_API_WHY_FIXTURE` set → `CitingNarrationModel` (`f1_api.why_fixture`)
       so `just verify-phase5`'s real Playwright process can assert grounded,
       cited explanations without a live API key;
    2. `ANTHROPIC_API_KEY` set → real `ClaudeHaikuModel`;
    3. otherwise a declining `MockNarrationModel` so CI / a developer with no
       key still get explanation envelopes (as the canonical fallback text)
       rather than a hard failure mid-stream.

    Overridden in unit tests with a scripted model so the suite never needs
    a live API key regardless of the environment."""
    global _narration_model
    if _narration_model is None:
        with _narration_model_lock:
            if _narration_model is None:
                if os.environ.get(WHY_FIXTURE_ENV_VAR):
                    _narration_model = CitingNarrationModel()
                elif os.environ.get("ANTHROPIC_API_KEY"):
                    _narration_model = ClaudeHaikuModel()
                else:
                    _narration_model = MockNarrationModel(
                        ModelNarration(
                            text="",
                            cited_evidence_ids=(),
                            no_clear_cause=True,
                        )
                    )
    return _narration_model


def get_explanation_store() -> ExplanationStore:
    """Process-level `MemoryExplanationStore` — explanations are never
    regenerated once cached (CLAUDE.md's Phase 5 budget invariant). Tests
    override with a fresh store so runs cannot leak explanations into each
    other."""
    global _explanation_store
    if _explanation_store is None:
        with _explanation_store_lock:
            if _explanation_store is None:
                _explanation_store = MemoryExplanationStore()
    return _explanation_store


NarrationModelDep = Annotated[NarrationModel, Depends(get_narration_model)]
ExplanationStoreDep = Annotated[ExplanationStore, Depends(get_explanation_store)]


@contextmanager
def why_stream_deps_cm(
    settings: Settings,
) -> Generator[tuple[NarrationModel, ExplanationStore, SearchIndex | None], None, None]:
    """The plain-function form the `/stream/{key}` WebSocket uses — same
    shape as `f1_api.radio.radio_store_cm`. Yields `(model, store, radio_index)`
    so historical replay can precompute explanations and live mode can
    generate on anomaly against the same cache.

    Note: this calls `get_narration_model` / `get_explanation_store`
    directly (not via FastAPI `Depends`), so `app.dependency_overrides`
    does not apply here — tests that exercise the WebSocket should
    monkeypatch these callables, or unit-test `replay_envelopes` with an
    explicit model/store (the radio enrichment tests' own pattern).
    """
    with radio_search_index_cm(settings) as radio_index:
        yield get_narration_model(), get_explanation_store(), radio_index


def _race_control_events(source: EventSource, session_key: SessionKey) -> tuple[DomainEvent, ...]:
    """Only the kinds `assemble_evidence` reads from the raw stream —
    race-control messages. Keeps the evidence load off the telemetry channel
    a full `source.events()` would otherwise pull in."""
    return tuple(source.events(session_key, kinds=["race_control_message"]))


def detect_session_anomalies(
    source: EventSource,
    session_key: SessionKey,
    *,
    report: KernelReport | None = None,
    event_name: str | None = None,
) -> tuple[KernelReport, list[Anomaly]]:
    """`analyze_session` (unless a report is already in hand) then
    `detect_anomalies` — the shared front half of both REST list and the
    explanation/timeline paths."""
    if report is None:
        report = analyze_session(source, session_key, KernelParams(), event_name=event_name)
    anomalies = detect_anomalies(report.frames, report, AnomalyParams())
    return report, anomalies


def explain_session_anomaly(
    source: EventSource,
    session_key: SessionKey,
    anomaly: Anomaly,
    model: NarrationModel,
    store: ExplanationStore,
    *,
    report: KernelReport | None = None,
    radio_index: SearchIndex | None = None,
    event_name: str | None = None,
) -> tuple[Explanation, EvidenceBundle]:
    """Assemble evidence for `anomaly` and return a cached explanation —
    never regenerates when the store already has this anomaly id."""
    if report is None:
        report, _ = detect_session_anomalies(source, session_key, event_name=event_name)
    sources = EvidenceSources(
        session_frames=report.frames,
        kernel_report=report,
        events=_race_control_events(source, session_key),
        radio_index=radio_index,
    )
    bundle = assemble_evidence(anomaly, sources, EvidenceParams())
    explanation = explain_cached(bundle, model, store, NarrationParams())
    return explanation, bundle


@dataclass(frozen=True, slots=True)
class ScheduledExplanation:
    """One precomputed explanation ready to pace onto the timeline at
    `anomaly.anomaly_time`."""

    anomaly: Anomaly
    explanation: Explanation
    cited: tuple[CitedEvidenceRef, ...]


def precompute_session_explanations(
    source: EventSource,
    session_key: SessionKey,
    model: NarrationModel,
    store: ExplanationStore,
    *,
    radio_index: SearchIndex | None = None,
    event_name: str | None = None,
) -> list[ScheduledExplanation]:
    """Historical-session batch: detect every anomaly, explain_cached each,
    return them sorted by `anomaly_time` for Delay Sync pacing. Live sessions
    call `explain_session_anomaly` per newly-seen anomaly instead — same
    cache either way."""
    report, anomalies = detect_session_anomalies(source, session_key, event_name=event_name)
    sources = EvidenceSources(
        session_frames=report.frames,
        kernel_report=report,
        events=_race_control_events(source, session_key),
        radio_index=radio_index,
    )
    scheduled: list[ScheduledExplanation] = []
    for anomaly in anomalies:
        bundle = assemble_evidence(anomaly, sources, EvidenceParams())
        explanation = explain_cached(bundle, model, store, NarrationParams())
        scheduled.append(
            ScheduledExplanation(
                anomaly=anomaly,
                explanation=explanation,
                cited=cited_evidence_refs(session_key, bundle, explanation),
            )
        )
    scheduled.sort(key=lambda item: (item.anomaly.anomaly_time, why_anomaly_id(item.anomaly)))
    return scheduled


def find_anomaly_by_why_id(anomalies: Sequence[Anomaly], why_id: str) -> Anomaly | None:
    """Locate the anomaly whose `f1_why.anomaly_id` matches `why_id`."""
    for anomaly in anomalies:
        if why_anomaly_id(anomaly) == why_id:
            return anomaly
    return None


__all__ = [
    "CitedEvidenceRef",
    "ExplanationStoreDep",
    "NarrationModelDep",
    "ScheduledExplanation",
    "anomaly_id_path",
    "cited_evidence_refs",
    "detect_session_anomalies",
    "evidence_href",
    "explain_session_anomaly",
    "find_anomaly_by_why_id",
    "get_explanation_store",
    "get_narration_model",
    "parse_anomaly_id",
    "precompute_session_explanations",
    "why_stream_deps_cm",
]
