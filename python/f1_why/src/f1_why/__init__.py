"""f1_why: Phase 5 — the why-engine.

Three packages, in the order data flows through them:

- `f1_why.detect` (5.1) — pure Python over `f1_kernel`'s typed frames and
  its `KernelReport`, deciding WHEN something in a session is worth
  explaining. No LLM, no evidence bundle, no I/O.
- `f1_why.evidence` (5.2) — given one `Anomaly`, gathers everything
  relevant AT OR BEFORE its own `anomaly_time` (kernel state, radio clips,
  race control) into one token-budgeted, self-contained `EvidenceBundle`.
  Still no LLM.
- `f1_why.story` — which detected moments a viewer sees: one card per
  incident, chosen by rules that work the same live and in a replay.
- `f1_why.narration` (5.3) — turns an `EvidenceBundle` into one grounded,
  cited sentence behind a structural grounding validator that never lets
  an ungrounded claim through. The default narrator is
  `f1_why.templates.TemplateNarrationModel`, which writes from the bundle's
  own numbers with no model call; a language model (Claude Haiku or the
  OpenRouter chain) is the optional alternative and the ONLY place one is
  used.

See each module's own docstring for the full reasoning, and CLAUDE.md's
Phase 5 invariants for why detection/evidence/narration are kept strictly
separate stages.

Typical use:

    from f1_domain import SessionKey, SessionType
    from f1_kernel import HistoricalSource, KernelParams, analyze_session
    from f1_why import (
        AnomalyParams,
        ClaudeHaikuModel,
        EvidenceParams,
        EvidenceSources,
        MemoryExplanationStore,
        NarrationParams,
        assemble_evidence,
        detect_anomalies,
        explain_cached,
    )

    source = HistoricalSource("data/historical")
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    report = analyze_session(source, key, KernelParams())
    anomalies = detect_anomalies(report.frames, report, AnomalyParams())

    sources = EvidenceSources(session_frames=report.frames, kernel_report=report)
    bundle = assemble_evidence(anomalies[0], sources, EvidenceParams())

    store = MemoryExplanationStore()
    explanation = explain_cached(bundle, ClaudeHaikuModel(), store, NarrationParams())
"""

from f1_why.anomaly import ANOMALY_SCHEMA, Anomaly, AnomalyKind, DetailValue, anomalies_to_frame
from f1_why.detect import detect_anomalies, starting_grid
from f1_why.evidence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceKind,
    EvidenceParams,
    EvidenceSources,
    anomaly_id,
    assemble_evidence,
)
from f1_why.narration import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    FALLBACK_TEXT,
    SYSTEM_PROMPT,
    BundleNarrationModel,
    ClaudeHaikuModel,
    Explanation,
    ExplanationConfidence,
    ExplanationStore,
    MemoryExplanationStore,
    MockNarrationModel,
    ModelNarration,
    NarrationModel,
    NarrationParams,
    Narrator,
    build_prompt,
    explain,
    explain_cached,
)
from f1_why.openrouter import (
    DEFAULT_OPENROUTER_MODEL,
    OpenRouterModel,
    openrouter_model_name,
    parse_narration_json,
)
from f1_why.params import AnomalyParams
from f1_why.store import NARRATION_VERSION, PostgresExplanationStore
from f1_why.story import StoryParams, is_story_moment, merge_incidents, select_story, story_key
from f1_why.templates import TemplateNarrationModel

__all__ = [
    "ANOMALY_SCHEMA",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_OPENROUTER_MODEL",
    "FALLBACK_TEXT",
    "NARRATION_VERSION",
    "SYSTEM_PROMPT",
    "Anomaly",
    "AnomalyKind",
    "AnomalyParams",
    "BundleNarrationModel",
    "ClaudeHaikuModel",
    "DetailValue",
    "EvidenceBundle",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceParams",
    "EvidenceSources",
    "Explanation",
    "ExplanationConfidence",
    "ExplanationStore",
    "MemoryExplanationStore",
    "MockNarrationModel",
    "ModelNarration",
    "NarrationModel",
    "NarrationParams",
    "Narrator",
    "OpenRouterModel",
    "PostgresExplanationStore",
    "StoryParams",
    "TemplateNarrationModel",
    "anomalies_to_frame",
    "anomaly_id",
    "assemble_evidence",
    "build_prompt",
    "detect_anomalies",
    "explain",
    "explain_cached",
    "is_story_moment",
    "merge_incidents",
    "openrouter_model_name",
    "parse_narration_json",
    "select_story",
    "starting_grid",
    "story_key",
]

__version__ = "0.0.1"
