"""f1_why: Phase 5 — the why-engine.

Three packages, in the order data flows through them:

- `f1_why.detect` (5.1) — pure Python over `f1_kernel`'s typed frames and
  its `KernelReport`, deciding WHEN something in a session is worth
  explaining. No LLM, no evidence bundle, no I/O.
- `f1_why.evidence` (5.2) — given one `Anomaly`, gathers everything
  relevant AT OR BEFORE its own `anomaly_time` (kernel state, radio clips,
  race control) into one token-budgeted, self-contained `EvidenceBundle`.
  Still no LLM.
- `f1_why.narration` (5.3) — the ONLY place an LLM is used: turns an
  `EvidenceBundle` into one grounded, cited sentence via a swappable
  `NarrationModel` (a real Claude Haiku backend, or a deterministic mock
  every test in this package uses), behind a structural grounding
  validator that never lets an ungrounded claim through.

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
from f1_why.detect import detect_anomalies
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
    ClaudeHaikuModel,
    Explanation,
    ExplanationConfidence,
    ExplanationStore,
    MemoryExplanationStore,
    MockNarrationModel,
    ModelNarration,
    NarrationModel,
    NarrationParams,
    build_prompt,
    explain,
    explain_cached,
)
from f1_why.params import AnomalyParams

__all__ = [
    "ANOMALY_SCHEMA",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "FALLBACK_TEXT",
    "SYSTEM_PROMPT",
    "Anomaly",
    "AnomalyKind",
    "AnomalyParams",
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
    "anomalies_to_frame",
    "anomaly_id",
    "assemble_evidence",
    "build_prompt",
    "detect_anomalies",
    "explain",
    "explain_cached",
]

__version__ = "0.0.1"
