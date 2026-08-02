"""`explain`: Phase 5.3 — the grounded narration step. Turns one
`EvidenceBundle` into ONE grounded, cited sentence (occasionally two) using
a small model. This is the ONLY place in the whole project an LLM is used,
and CLAUDE.md's own line for this prompt is that it stays tightly
constrained: the model explains RETRIEVED evidence, it never asserts a fact
that is not in the bundle.

Three pieces, in the order a call actually flows through them:

- **`NarrationModel`** — a narrow Protocol (`complete(prompt) -> ModelNarration`),
  the same "structural interface, swap the backend" split this project
  already uses for `f1_radio`'s `WhisperBackend`/`EmbeddingBackend`.
  `ClaudeHaikuModel` is the real backend — Claude Haiku, per CLAUDE.md's own
  Phase 5 budget invariant ("a small fast model ... a few calls per race
  weekend") — and imports `anthropic` LAZILY, the same "importing this
  module never requires the real dependency" precedent `f1_radio.
  transcription.load_model`/`f1_radio.embeddings.load_embedding_model`
  already set for faster-whisper/sentence-transformers. `MockNarrationModel`
  is a deterministic, scripted fake (mirroring `FakeWhisperBackend`'s own
  precedent — a plain scripted object, not a mock-library double) that
  every test in this package uses; no test needs a live API key.
- **`build_prompt` + `explain`** — `build_prompt` is a PURE function
  (`EvidenceBundle -> str`), independently testable/reviewable with no
  model involved: it renders the anomaly plus every surviving evidence
  item, each tagged with its own stable id, and instructs the model to
  cite only what it used. `explain` is the one call to the model: it
  builds the prompt, calls `model.complete` once — UNLESS
  `bundle.low_evidence` already says there is nothing to explain, in which
  case the model is never even called (CLAUDE.md's "keep calls few and
  cheap", taken literally) — then runs the grounding validator.
- **The grounding validator** (`_validate_grounding`) — deliberately a set
  of STRUCTURAL, deterministic checks, never a second LLM call (that would
  both violate "the ONLY place an LLM is used" and double the cost this
  phase is explicitly budgeted against): every cited id must actually be
  in the bundle, and a substantive explanation must cite at least one id.
  A `low_evidence` bundle is handled one layer up, by `explain` itself,
  which never calls the model at all in that case — so no narration ever
  reaches the validator with nothing to validate against. Any failure —
  or the model's own honest `no_clear_cause` — is normalized to the SAME
  safe fallback text and a `LOW` confidence, so an ungrounded claim can
  never reach a caller on any path.

Confidence propagates: `_derive_confidence` looks at the WORST `confidence`
among the evidence items the model actually cited (never the best) — an
explanation is only as trustworthy as its weakest cited source, the same
"never hide low confidence inside an average" discipline `f1_kernel`
applies to fit reliability.

Explanations are cached per anomaly id (`ExplanationStore`/
`explain_cached`) — never regenerated once computed, the other half of
"keep calls few and cheap".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from f1_why.evidence import EvidenceBundle, EvidenceItem

DEFAULT_MODEL = "claude-haiku-4-5"
"""CLAUDE.md's own Phase 5 budget invariant names Haiku explicitly — this
is not a default chosen by this module, it is the project's own decision."""

DEFAULT_MAX_TOKENS = 300
"""One or two sentences, generously bounded — this is a narration call, not
an essay."""

FALLBACK_TEXT = "No clear cause found in the available evidence."
"""The one, canonical "we don't know" sentence — every path that declines
to explain (a weak bundle, the model's own admission, a failed grounding
check) normalizes to this SAME string, so a caller/UI can rely on exact
text rather than parsing varying LLM-authored "I'm not sure" prose."""

SYSTEM_PROMPT = (
    "You explain notable moments in a Formula 1 race to a fan watching a "
    "delayed broadcast. You are given one anomaly and a numbered list of "
    "EVIDENCE items, each with a stable id. You may state ONLY facts that "
    "appear in that evidence — never guess, never infer beyond what is "
    "written, and never use outside knowledge of real F1 drivers, teams or "
    "races. Write ONE sentence (two only if genuinely needed) giving the "
    "most plausible cause of the anomaly, grounded entirely in the cited "
    "evidence. List the id of every evidence item your sentence actually "
    "relies on in cited_evidence_ids — never cite an item you did not use, "
    "and never leave cited_evidence_ids empty if you wrote an explanation. "
    "If the evidence does not support any real explanation, set "
    "no_clear_cause to true and leave text and cited_evidence_ids empty — "
    "never invent a plausible-sounding cause to fill the gap."
)


class ExplanationConfidence(StrEnum):
    """How much to trust an `Explanation` — deliberately its OWN type, not
    a reuse of `f1_kernel.undercut.Confidence` (same shape, a different
    question: that one answers "is this crossover call trustworthy",
    this one answers "is this NARRATION well-grounded", and the two must
    not silently move together if one taxonomy is retuned — the same
    reasoning `f1_why.params.AnomalyParams` already gives for not reusing
    `KernelParams`)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class ModelNarration:
    """What a model backend hands back — raw, not yet grounding-checked.
    `explain` is what turns this into a trustworthy `Explanation`."""

    text: str
    cited_evidence_ids: tuple[str, ...]
    no_clear_cause: bool
    """The model's OWN claim that the bundle does not support an
    explanation — distinct from the grounding validator discovering the
    same thing after the fact when the model tried to explain anyway."""


class NarrationModel(Protocol):
    """One call, prompt in, raw narration out — narrow enough that a real
    API-backed class and a scripted fake both satisfy it with no shared
    base class, the same protocol-per-backend split `f1_radio.
    transcription.WhisperBackend`/`f1_radio.embeddings.EmbeddingBackend`
    already use in this project."""

    def complete(self, prompt: str) -> ModelNarration: ...


@dataclass(frozen=True, slots=True)
class Explanation:
    """The end product: one sentence a caller may show a user, plus enough
    provenance to render a citation trail and a confidence badge."""

    anomaly_id: str
    text: str
    cited_evidence_ids: tuple[str, ...]
    confidence: ExplanationConfidence
    grounded: bool
    """True iff `text` is a real, cited explanation that passed the
    grounding validator — False whenever `text` is the safe fallback,
    whatever the reason (see `fallback_reason`)."""
    fallback_reason: str | None
    """Why the fallback was used, for a debugging session or a UI's own
    "why no explanation" tooltip — `None` exactly when `grounded` is True."""


class NarrationParams(BaseModel):
    """Confidence-derivation tuning only — model/backend configuration
    (which model, `max_tokens`) lives on the `NarrationModel` backend
    itself (`ClaudeHaikuModel`'s own constructor), the same split
    `f1_radio.transcription.load_model(model_size=..., device=...)` keeps
    between backend config and the pure orchestration around it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    high_confidence_floor: float = Field(
        default=0.8,
        gt=0,
        le=1,
        description=(
            "The worst cited evidence confidence must be at least this "
            "high for the explanation to be HIGH. STRUCTURAL, not "
            "calibrated against a measured corpus of good/bad explanations."
        ),
    )
    medium_confidence_floor: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description=(
            "Below `high_confidence_floor` but at or above this is MEDIUM; below it is LOW."
        ),
    )

    def with_overrides(self, **overrides: object) -> NarrationParams:
        """A copy with `overrides` applied, re-validated — matching every
        other params type in this project."""
        return NarrationParams.model_validate({**self.model_dump(), **overrides})


def build_prompt(bundle: EvidenceBundle) -> str:
    """Pure: renders `bundle` into the exact text sent to the model — no
    model call, no randomness, independently reviewable/testable."""
    anomaly = bundle.anomaly
    drivers = ", ".join(anomaly.drivers) if anomaly.drivers else "no driver named"
    lines = [
        f"Anomaly: {anomaly.kind.value}, driver(s) {drivers}, lap "
        f"{anomaly.lap_number}, at {anomaly.anomaly_time.isoformat()}.",
        "",
        "Evidence (cite by id; state nothing that is not listed here):",
    ]
    for item in bundle.items:
        driver_part = f" driver(s) {', '.join(item.drivers)}," if item.drivers else ""
        lines.append(
            f"- [{item.evidence_id}] ({item.kind.value},{driver_part} "
            f"confidence={item.confidence_label}, {item.event_time.isoformat()}): {item.summary}"
        )
    if bundle.low_evidence:
        lines.extend(
            [
                "",
                "Note: this bundle has little or no evidence beyond the anomaly's own "
                "numbers. If nothing above plausibly explains it, set no_clear_cause=true.",
            ]
        )
    return "\n".join(lines)


def _validate_grounding(narration: ModelNarration, bundle: EvidenceBundle) -> str | None:
    """`None` when `narration` is fully grounded in `bundle`; otherwise the
    reason it was rejected. Deliberately structural/deterministic — see the
    module docstring for why this is never a second model call.

    Assumes `narration` is already a SUBSTANTIVE attempt at an explanation
    (`no_clear_cause=False`) over a bundle that is not `low_evidence`:
    `explain` is this function's only caller, and it already returns before
    ever reaching this call in either of those two states (see `explain`'s
    own docstring). Re-checking them here would be validating states this
    private function structurally cannot observe.
    """
    if not narration.cited_evidence_ids:
        return "explanation given with no cited evidence"

    known_ids = {item.evidence_id for item in bundle.items}
    unknown = [eid for eid in narration.cited_evidence_ids if eid not in known_ids]
    if unknown:
        return f"cited evidence id(s) not in the bundle: {unknown}"

    return None


def _derive_confidence(
    cited_items: Sequence[EvidenceItem], params: NarrationParams
) -> ExplanationConfidence:
    """The WORST confidence among the CITED items — never the best, and
    never the whole bundle's (an uncited low-confidence item should not
    drag down an explanation that never relied on it, and a cited one
    always should)."""
    if not cited_items:
        return ExplanationConfidence.LOW
    worst = min(item.confidence for item in cited_items)
    if worst >= params.high_confidence_floor:
        return ExplanationConfidence.HIGH
    if worst >= params.medium_confidence_floor:
        return ExplanationConfidence.MEDIUM
    return ExplanationConfidence.LOW


def _fallback_explanation(anomaly_id: str, reason: str) -> Explanation:
    return Explanation(
        anomaly_id=anomaly_id,
        text=FALLBACK_TEXT,
        cited_evidence_ids=(),
        confidence=ExplanationConfidence.LOW,
        grounded=False,
        fallback_reason=reason,
    )


def explain(bundle: EvidenceBundle, model: NarrationModel, params: NarrationParams) -> Explanation:
    """One explanation for `bundle`. Calls `model.complete` AT MOST once —
    never at all when `bundle.low_evidence` already guarantees the answer
    is "no clear cause" — and the grounding validator sits between every
    model response and the value this returns, on every path, so an
    ungrounded claim can never reach a caller.
    """
    if bundle.low_evidence:
        return _fallback_explanation(bundle.anomaly_id, "low_evidence")

    narration = model.complete(build_prompt(bundle))

    if narration.no_clear_cause:
        return _fallback_explanation(bundle.anomaly_id, "model_declined")

    rejection = _validate_grounding(narration, bundle)
    if rejection is not None:
        return _fallback_explanation(bundle.anomaly_id, rejection)

    cited_ids = set(narration.cited_evidence_ids)
    cited_items = [item for item in bundle.items if item.evidence_id in cited_ids]
    return Explanation(
        anomaly_id=bundle.anomaly_id,
        text=narration.text.strip(),
        cited_evidence_ids=tuple(narration.cited_evidence_ids),
        confidence=_derive_confidence(cited_items, params),
        grounded=True,
        fallback_reason=None,
    )


class ExplanationStore(Protocol):
    """Where cached explanations live, keyed by anomaly id — narrow enough
    that an in-memory cache and a future persistent store (mirroring
    `f1_radio.store.TranscriptStore`'s own Postgres/Memory split) both
    satisfy it with no shared base class."""

    def get(self, anomaly_id: str) -> Explanation | None: ...

    def set(self, anomaly_id: str, explanation: Explanation) -> None: ...


class MemoryExplanationStore:
    """An in-memory `ExplanationStore` — for tests and single-process use,
    the same `Memory*` precedent every store/index in this project sets."""

    def __init__(self) -> None:
        self._cache: dict[str, Explanation] = {}

    def get(self, anomaly_id: str) -> Explanation | None:
        return self._cache.get(anomaly_id)

    def set(self, anomaly_id: str, explanation: Explanation) -> None:
        self._cache[anomaly_id] = explanation


def explain_cached(
    bundle: EvidenceBundle,
    model: NarrationModel,
    store: ExplanationStore,
    params: NarrationParams,
) -> Explanation:
    """`explain`, but never twice for the same anomaly — CLAUDE.md's own
    "cache explanations by anomaly id; never regenerate". A cache hit calls
    neither the model nor `explain` at all."""
    cached = store.get(bundle.anomaly_id)
    if cached is not None:
        return cached
    explanation = explain(bundle, model, params)
    store.set(bundle.anomaly_id, explanation)
    return explanation


class MockNarrationModel:
    """A deterministic, scripted model for tests — never touches a network.
    Returns `response` for every call regardless of the prompt, mirroring
    `f1_radio.transcription`'s own `FakeWhisperBackend` precedent (a plain
    scripted fake, not a mock-library double). `calls` records every prompt
    it was actually asked to complete, so a test can assert `explain`'s own
    "never call the model when low_evidence" short-circuit — and
    `explain_cached`'s "never call it twice" — for real, not just by
    inspecting the returned `Explanation`.
    """

    def __init__(self, response: ModelNarration) -> None:
        self.response = response
        self.calls: list[str] = []

    def complete(self, prompt: str) -> ModelNarration:
        self.calls.append(prompt)
        return self.response


class _NarrationResponseSchema(BaseModel):
    """The structured-output shape `ClaudeHaikuModel` asks the model for —
    see `anthropic`'s `messages.parse`/`output_config.format` docs. Mirrors
    `ModelNarration` field-for-field; kept as a separate type since this one
    is an API wire contract and `ModelNarration` is this module's own."""

    text: str
    cited_evidence_ids: list[str]
    no_clear_cause: bool


class ClaudeHaikuModel:
    """The real backend: one structured-output `messages.parse` call per
    explanation, against Claude Haiku (`DEFAULT_MODEL`) per CLAUDE.md's own
    Phase 5 budget invariant. `anthropic` is imported LAZILY inside
    `__init__` — the same "importing this module never requires the real,
    optional dependency" precedent `f1_radio.transcription.load_model`/
    `f1_radio.embeddings.load_embedding_model` already set for
    faster-whisper/sentence-transformers — so the whole test suite (which
    only ever exercises `MockNarrationModel`) never needs `anthropic`
    installed, let alone a live API key.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        import anthropic

        # Untyped past this point: the Anthropic SDK's client surface moves
        # fast enough that pinning a strict type here would fight every
        # SDK upgrade, and this class is never imported by anything the
        # test suite exercises (see the class docstring) — the same
        # `Any`-typed-model tradeoff `f1_radio.embeddings.
        # _SentenceTransformerBackend` already makes for the same reason.
        self._client: Any = anthropic.Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, prompt: str) -> ModelNarration:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=_NarrationResponseSchema,
        )
        parsed: _NarrationResponseSchema = response.parsed_output
        return ModelNarration(
            text=parsed.text,
            cited_evidence_ids=tuple(parsed.cited_evidence_ids),
            no_clear_cause=parsed.no_clear_cause,
        )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "FALLBACK_TEXT",
    "SYSTEM_PROMPT",
    "ClaudeHaikuModel",
    "Explanation",
    "ExplanationConfidence",
    "ExplanationStore",
    "MemoryExplanationStore",
    "MockNarrationModel",
    "ModelNarration",
    "NarrationModel",
    "NarrationParams",
    "build_prompt",
    "explain",
    "explain_cached",
]
