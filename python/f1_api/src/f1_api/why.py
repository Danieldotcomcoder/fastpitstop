"""The seam for Phase 5.4: resolving the why-engine (`f1_why`) the same way
`f1_api.radio` resolves the team-radio corpus.

`get_narration_model` / `get_explanation_store` are real FastAPI `Depends`
providers — the injection seam tests need to swap in a `MockNarrationModel`
and a fresh `MemoryExplanationStore`, so no test ever needs a live API key
(CLAUDE.md's Phase 5 invariant). Production resolves to a process-level
narration model (see `get_narration_model`) and, whenever a store DSN is
configured, a `PostgresExplanationStore` so explanations outlive the process
-- the same Memory/Postgres split `f1_radio.store` makes.

Orchestration helpers (`detect_session_anomalies`, `explain_session_anomaly`,
`precompute_session_explanations`) keep every route and the WebSocket
replay path from re-deriving the analyze → detect → assemble → explain
wiring by hand — the same "one place owns the chain" discipline
`f1_kernel.analyze_session` already sets for the kernel itself.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends

from f1_api.config import WHY_FIXTURE_ENV_VAR, Settings, get_settings
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
    Narrator,
    OpenRouterModel,
    PostgresExplanationStore,
    StoryParams,
    TemplateNarrationModel,
    assemble_evidence,
    detect_anomalies,
    explain_cached,
    select_story,
    starting_grid,
)
from f1_why import (
    anomaly_id as why_anomaly_id,
)
from f1_why.openrouter import API_KEY_ENV_VAR as OPENROUTER_API_KEY_ENV_VAR

_log = logging.getLogger(__name__)

_narration_model: Narrator | None = None
_narration_model_lock = Lock()
_explanation_store: ExplanationStore | None = None
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


NARRATOR_ENV_VAR = "F1_WHY_NARRATOR"
"""`llm` to narrate with a language model (OpenRouter, else Anthropic) instead
of the default `TemplateNarrationModel`."""


def _try_backend(factory: Callable[[], NarrationModel], label: str) -> NarrationModel | None:
    """Construct a real backend, or log and return `None` so the caller
    falls through to the declining mock. Constructing one reaches for an
    optional dependency (`httpx`, `anthropic`) and validates a key, either
    of which can fail on a misconfigured box — and an explanation that
    degrades to "no clear cause" is a far better failure than every
    why-engine route returning 500."""
    try:
        return factory()
    except Exception:
        _log.warning("could not construct the %s narration backend", label, exc_info=True)
        return None


def get_narration_model() -> Narrator:
    """Process-level lazy singleton. Resolution order:

    1. `F1_API_WHY_FIXTURE` set → `CitingNarrationModel` (`f1_api.why_fixture`)
       so `just verify-phase5`'s real Playwright process can assert grounded,
       cited explanations without a live API key;
    2. unless `F1_WHY_NARRATOR=llm`, `TemplateNarrationModel`: sentences built
       from each bundle's own numbers, free and instant, and the default.
       The free models it replaced wrote the same sentence for every
       overtake and ran out of their daily allowance mid-race;
    3. `OPENROUTER_API_KEY` set → `OpenRouterModel`, walking
       `f1_why.openrouter.DEFAULT_OPENROUTER_MODEL_CHAIN` (or
       `OPENROUTER_MODELS`). Checked BEFORE Anthropic
       because it is the deployment this project actually runs on; a box
       with both keys set can pick explicitly by unsetting one.
    4. `ANTHROPIC_API_KEY` set → real `ClaudeHaikuModel`;
    5. otherwise a declining `MockNarrationModel` so CI / a developer with no
       key still get explanation envelopes (as the canonical fallback text)
       rather than a hard failure mid-stream.

    A backend that cannot be constructed (a missing optional dependency,
    say) falls through to the declining mock rather than 500-ing every
    explanation route — the why-engine is an enrichment, never the reason
    the rest of the API stops working.

    Overridden in unit tests with a scripted model so the suite never needs
    a live API key regardless of the environment."""
    global _narration_model
    if _narration_model is None:
        with _narration_model_lock:
            if _narration_model is None:
                if os.environ.get(WHY_FIXTURE_ENV_VAR):
                    _narration_model = CitingNarrationModel()
                elif os.environ.get(NARRATOR_ENV_VAR, "").strip().lower() != "llm":
                    _narration_model = TemplateNarrationModel()
                elif os.environ.get(OPENROUTER_API_KEY_ENV_VAR):
                    _narration_model = _try_backend(OpenRouterModel, "OpenRouter")
                elif os.environ.get("ANTHROPIC_API_KEY"):
                    _narration_model = _try_backend(ClaudeHaikuModel, "Anthropic")
                if _narration_model is None:
                    _narration_model = MockNarrationModel(
                        ModelNarration(
                            text="",
                            cited_evidence_ids=(),
                            no_clear_cause=True,
                        )
                    )
    return _narration_model


def get_explanation_store() -> ExplanationStore:
    """The process-level explanation cache. Explanations are never
    regenerated once cached (CLAUDE.md's Phase 5 budget invariant).

    Postgres-backed whenever a store DSN is configured, so a moment is
    narrated once ever rather than once per process: the narrator's free
    tier allows 50 requests a day, and an in-memory cache was emptied by
    every deploy. `MemoryExplanationStore` otherwise (a developer machine,
    CI). Tests override with a fresh store so runs cannot leak explanations
    into each other."""
    global _explanation_store
    if _explanation_store is None:
        with _explanation_store_lock:
            if _explanation_store is None:
                dsn = get_settings().store_dsn
                _explanation_store = (
                    PostgresExplanationStore(dsn) if dsn else MemoryExplanationStore()
                )
    return _explanation_store


def reset_narration_model() -> None:
    """Drop the resolved backend so the next `get_narration_model` picks one
    again. Exists for tests that change the environment between cases: the
    singleton is deliberately process-level (constructing a backend opens an
    HTTP client), so without this the FIRST test's resolution would leak
    into every later one."""
    global _narration_model
    with _narration_model_lock:
        _narration_model = None


NarrationModelDep = Annotated[Narrator, Depends(get_narration_model)]
ExplanationStoreDep = Annotated[ExplanationStore, Depends(get_explanation_store)]


@contextmanager
def why_stream_deps_cm(
    settings: Settings,
) -> Generator[tuple[Narrator, ExplanationStore, SearchIndex | None], None, None]:
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


EVIDENCE_EVENT_KINDS = ("race_control_message", "driver_list_updated")
"""The raw-stream kinds `assemble_evidence` reads: race-control messages as
evidence, and the driver list so a narrator can name who "63" is."""


def _evidence_events(source: EventSource, session_key: SessionKey) -> tuple[DomainEvent, ...]:
    """Only the kinds `assemble_evidence` reads from the raw stream. Keeps
    the evidence load off the telemetry channel a full `source.events()`
    would otherwise pull in."""
    return tuple(source.events(session_key, kinds=list(EVIDENCE_EVENT_KINDS)))


def detect_session_anomalies(
    source: EventSource,
    session_key: SessionKey,
    *,
    report: KernelReport | None = None,
    event_name: str | None = None,
) -> tuple[KernelReport, list[Anomaly]]:
    """`analyze_session` (unless a report is already in hand) then
    `detect_anomalies` — the shared front half of both REST list and the
    explanation/timeline paths. The starting grid comes from the session's
    own position stream (`f1_why.starting_grid`), the same way the live hub
    derives it."""
    if report is None:
        report = analyze_session(source, session_key, KernelParams(), event_name=event_name)
    grid = starting_grid(source.events(session_key, kinds=["position_changed"]), report.frames.laps)
    anomalies = detect_anomalies(report.frames, report, AnomalyParams(), grid=grid)
    return report, anomalies


def explain_session_anomaly(
    source: EventSource,
    session_key: SessionKey,
    anomaly: Anomaly,
    model: Narrator,
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
        events=_evidence_events(source, session_key),
        radio_index=radio_index,
    )
    bundle = assemble_evidence(anomaly, sources, EvidenceParams())
    explanation = explain_cached(bundle, model, store, NarrationParams())
    return explanation, bundle


@dataclass(frozen=True, slots=True)
class ScheduledExplanation:
    """One precomputed explanation ready to pace onto the timeline at
    `anomaly.anomaly_time`.

    Carries both citation shapes because the two consumers need different
    ones and neither should re-derive the other's: `cited` is the
    lightweight id+href form the WebSocket puts on the wire, `bundle` is
    the full evidence the REST story route renders as `CitedEvidenceOut`
    (event time, drivers, summary, confidence). Keeping the bundle costs
    nothing here — it was just built to produce the explanation."""

    anomaly: Anomaly
    explanation: Explanation
    cited: tuple[CitedEvidenceRef, ...]
    bundle: EvidenceBundle


NARRATION_CONCURRENCY = 4
"""Model calls in flight at once while narrating one session. Enough to turn
a dozen serial round trips into three, and well inside OpenRouter's free-tier
rate limit (20 requests a minute) for a single story."""


def most_salient(anomalies: Sequence[Anomaly], limit: int | None) -> list[Anomaly]:
    """The `limit` most salient anomalies, still in `detect_anomalies`' own
    (salience-descending, deterministically tie-broken) order.

    `detect_anomalies` is deliberately uncapped — it reports everything it
    finds — but NARRATING everything is neither affordable nor readable: a
    real 2024 race detects ~300 anomalies, one model call each, and a
    300-item "story" is a log, not a story. `None` means no cap (the
    original behaviour, kept for callers that genuinely want every one)."""
    if limit is None:
        return list(anomalies)
    return list(anomalies[: max(0, limit)])


def story_moments(anomalies: Sequence[Anomaly], limit: int | None) -> list[Anomaly]:
    """The moments a viewer sees, in race order: `f1_why.select_story`, with
    `limit` (the server's `F1_WHY_MAX_EXPLANATIONS` budget) as its ceiling.

    It used to be the most salient anomalies with each kind capped at a
    third. Ranking a whole race by salience cannot work live (the race is
    not over), and the anomalies it ranked were mostly pit-cycle reshuffles
    reported as overtakes; `select_story`'s rules look at each moment
    alone, so live and replay show the same cards."""
    params = StoryParams() if limit is None else StoryParams(max_moments=max(1, limit))
    return select_story(anomalies, params)


SHOWN_FALLBACKS = frozenset({"provider_unavailable"})
"""The fallback reasons still worth a card: an outage is news about the
service, said honestly. A narrator that found nothing to say about a moment
("model_declined", "low_evidence") means the moment has no story; showing
"no clear cause found" for it was most of what made the old story read as
noise."""


def worth_showing(explanation: Explanation) -> bool:
    """Whether an explanation earns a card (see `SHOWN_FALLBACKS`)."""
    return explanation.grounded or explanation.fallback_reason in SHOWN_FALLBACKS


def precompute_session_explanations(
    source: EventSource,
    session_key: SessionKey,
    model: Narrator,
    store: ExplanationStore,
    *,
    radio_index: SearchIndex | None = None,
    event_name: str | None = None,
    max_explanations: int | None = None,
) -> list[ScheduledExplanation]:
    """Historical-session batch: detect anomalies, explain_cached the story
    (`story_moments`), return the moments worth a card (`worth_showing`)
    sorted by `anomaly_time` for Delay Sync pacing. Live sessions call
    `explain_session_anomaly` per newly-seen moment instead — same cache
    either way."""
    report, anomalies = detect_session_anomalies(source, session_key, event_name=event_name)
    sources = EvidenceSources(
        session_frames=report.frames,
        kernel_report=report,
        events=_evidence_events(source, session_key),
        radio_index=radio_index,
    )
    picked = story_moments(anomalies, max_explanations)
    bundles = [assemble_evidence(anomaly, sources, EvidenceParams()) for anomaly in picked]

    # The model calls are independent network round trips, so they run side
    # by side. One after another, a twelve-moment story cost twelve serial
    # calls: at the old free-tier latency that was 157 seconds before the
    # story rendered at all.
    def narrate(bundle: EvidenceBundle) -> Explanation:
        return explain_cached(bundle, model, store, NarrationParams())

    with ThreadPoolExecutor(max_workers=NARRATION_CONCURRENCY) as pool:
        explanations = list(pool.map(narrate, bundles))
    scheduled = [
        ScheduledExplanation(
            anomaly=anomaly,
            explanation=explanation,
            cited=cited_evidence_refs(session_key, bundle, explanation),
            bundle=bundle,
        )
        for anomaly, bundle, explanation in zip(picked, bundles, explanations, strict=True)
        if worth_showing(explanation)
    ]
    scheduled.sort(key=lambda item: (item.anomaly.anomaly_time, why_anomaly_id(item.anomaly)))
    return scheduled


class ExplanationSchedule:
    """A thread-safe hand-off for a session's precomputed explanation batch.

    Why this exists: `precompute_session_explanations` costs seconds even
    capped (a full `analyze_session` + `detect_anomalies`, then one evidence
    assembly per narrated anomaly), and it used to run INLINE at the top of
    `replay_envelopes` — before a single domain event was yielded. On a real
    2024 race that meant ~121 s of a blank, silent dashboard before the
    first envelope arrived, which read as "the app is broken" and, at
    `speed>=20`, was most of the interesting part of the race.

    So the batch now runs on its own thread (with its OWN `EventSource`, so
    no source or database connection is ever touched by two threads at
    once) while the event stream is already flowing. `items()` returns
    `None` until the batch lands; the replay loop simply emits no
    explanations until then, and once the batch arrives every anomaly whose
    moment has already passed flushes at the next event.

    Spoiler-safety is unaffected: an explanation arriving LATE is always
    safe (it is paced to at-or-after its own `anomaly_time`, and gets the
    same wall-clock `event_time_utc` remap as every other envelope). Only
    arriving EARLY would leak, and nothing here can make that happen."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: list[ScheduledExplanation] | None = None
        self._failed: BaseException | None = None

    def set(self, items: Sequence[ScheduledExplanation]) -> None:
        with self._lock:
            self._items = list(items)

    def fail(self, error: BaseException) -> None:
        """Record that the batch could not be built. The stream continues
        without explanations rather than dying — the why-engine is an
        enrichment, never the reason a race is watchable."""
        with self._lock:
            self._failed = error
            self._items = []

    def items(self) -> list[ScheduledExplanation] | None:
        """The batch, or `None` while it is still being computed."""
        with self._lock:
            return self._items

    @property
    def error(self) -> BaseException | None:
        with self._lock:
            return self._failed


def find_anomaly_by_why_id(anomalies: Sequence[Anomaly], why_id: str) -> Anomaly | None:
    """Locate the anomaly whose `f1_why.anomaly_id` matches `why_id`."""
    for anomaly in anomalies:
        if why_anomaly_id(anomaly) == why_id:
            return anomaly
    return None


__all__ = [
    "CitedEvidenceRef",
    "ExplanationSchedule",
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
    "most_salient",
    "parse_anomaly_id",
    "precompute_session_explanations",
    "reset_narration_model",
    "story_moments",
    "why_stream_deps_cm",
    "worth_showing",
]
