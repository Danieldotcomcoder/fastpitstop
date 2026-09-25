"""`explain`/`explain_cached` against hand-built bundles — narration is
tested in isolation from evidence ASSEMBLY (`test_evidence.py`'s own job):
every bundle here is constructed directly, so these tests probe exactly one
thing each — the grounding validator, confidence derivation, the
low-evidence/model-declined/caching short-circuits — with no dependency on
the kernel fixture or a real search index.

All tests run against `MockNarrationModel`; none needs a live API key,
matching CLAUDE.md's own Phase 5 invariant for this prompt.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from f1_domain import DriverRef
from f1_why.anomaly import Anomaly, AnomalyKind
from f1_why.evidence import EvidenceBundle, EvidenceItem, EvidenceKind
from f1_why.narration import (
    FALLBACK_TEXT,
    PROVIDER_UNAVAILABLE_TEXT,
    Explanation,
    ExplanationConfidence,
    MemoryExplanationStore,
    MockNarrationModel,
    ModelNarration,
    NarrationParams,
    build_prompt,
    explain,
    explain_cached,
)

_T0 = datetime(2030, 1, 1, 13, 12, 0, tzinfo=UTC)


def _anomaly(**overrides: object) -> Anomaly:
    fields: dict[str, object] = {
        "kind": AnomalyKind.PACE_LOSS,
        "lap_number": 8,
        "anomaly_time": _T0,
        "drivers": ("44",),
        "magnitude": 8.0,
        "salience": 0.9,
        "summary": {"delta_s": 8.0},
        "reasons": ("lap 8 was 8.00s slower than driver 44's own recent trend",),
    }
    fields.update(overrides)
    return Anomaly(**fields)  # type: ignore[arg-type]


def _item(
    kind: EvidenceKind, evidence_id: str, confidence: float, **overrides: object
) -> EvidenceItem:
    fields: dict[str, object] = {
        "evidence_id": evidence_id,
        "kind": kind,
        "event_time": _T0,
        "drivers": ("44",),
        "summary": f"{kind.value} summary",
        "data": {},
        "confidence": confidence,
        "confidence_label": "measured",
        "salience": 0.5,
        "source": "test",
    }
    fields.update(overrides)
    return EvidenceItem(**fields)  # type: ignore[arg-type]


def _bundle(items: tuple[EvidenceItem, ...], *, low_evidence: bool) -> EvidenceBundle:
    return EvidenceBundle(
        anomaly=_anomaly(),
        anomaly_id="pace_loss:8:44:123",
        items=items,
        low_evidence=low_evidence,
        token_budget=600,
        estimated_tokens=42,
        notes=(),
    )


_ANOMALY_FACT = _item(EvidenceKind.ANOMALY_FACT, "anomaly", 1.0, confidence_label="measured")


# ---------------------------------------------------------------------------
# a clear-cause bundle yields an explanation citing both evidence ids
# ---------------------------------------------------------------------------


def test_clear_cause_bundle_yields_explanation_citing_both_evidence_ids() -> None:
    radio = _item(
        EvidenceKind.RADIO_CLIP,
        "radio_clip:44:t1",
        0.85,
        summary='driver 44 radio: "tyres are gone, box this lap"',
        confidence_label="transcribed",
    )
    deg = _item(
        EvidenceKind.DEGRADATION_FIT,
        "degradation_fit:44:1",
        0.6,
        summary="driver 44's stint 1 pace has been trending +0.400s/lap (thin fit).",
        confidence_label="thin",
    )
    bundle = _bundle((_ANOMALY_FACT, radio, deg), low_evidence=False)
    scripted = ModelNarration(
        text="The tyres were failing, matching both the radio call and the rising pace trend.",
        cited_evidence_ids=(radio.evidence_id, deg.evidence_id),
        no_clear_cause=False,
    )
    model = MockNarrationModel(scripted)

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.grounded
    assert explanation.text == scripted.text
    assert set(explanation.cited_evidence_ids) == {radio.evidence_id, deg.evidence_id}
    # Confidence reflects the WORST cited item (the deg fit's 0.6 "thin"),
    # never the best (the radio clip's 0.85) and never the anomaly fact's
    # own 1.0 (uncited, so it must not move this number at all).
    assert explanation.confidence is ExplanationConfidence.MEDIUM
    assert model.calls == [build_prompt(bundle)]


# ---------------------------------------------------------------------------
# a near-empty bundle yields the fallback, never an invention
# ---------------------------------------------------------------------------


def test_near_empty_bundle_yields_fallback_and_never_calls_the_model() -> None:
    bundle = _bundle((_ANOMALY_FACT,), low_evidence=True)
    model = MockNarrationModel(
        ModelNarration(
            text="this text must never be shown",
            cited_evidence_ids=("anomaly",),
            no_clear_cause=False,
        )
    )

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.text == FALLBACK_TEXT
    assert not explanation.grounded
    assert explanation.fallback_reason == "low_evidence"
    assert explanation.cited_evidence_ids == ()
    assert explanation.confidence is ExplanationConfidence.LOW
    # CLAUDE.md's "keep calls few and cheap": a bundle already known to have
    # no cause must never spend a model call finding that out again.
    assert model.calls == []


def test_model_declining_yields_the_same_fallback_text() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True))

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.text == FALLBACK_TEXT
    assert not explanation.grounded
    assert explanation.fallback_reason == "model_declined"
    # The model WAS asked here (unlike the low_evidence short-circuit) --
    # it just honestly declined.
    assert len(model.calls) == 1


def test_a_provider_outage_is_not_reported_as_a_verdict() -> None:
    """The distinction this whole engine rests on. "No clear cause found in
    the available evidence" is a claim ABOUT THE RACE: we looked, and the
    evidence does not support one. A backend that never answered supports no
    claim at all, and must say so in different words -- during the 2026
    Spanish GP an overloaded free-tier GPU pool produced the confident
    version for every anomaly of the race."""
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(
        ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True, provider_failed=True)
    )

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.text == PROVIDER_UNAVAILABLE_TEXT
    assert explanation.text != FALLBACK_TEXT
    assert explanation.fallback_reason == "provider_unavailable"
    assert not explanation.grounded


# ---------------------------------------------------------------------------
# the grounding validator catches a deliberately ungrounded response
# ---------------------------------------------------------------------------


def test_grounding_validator_rejects_a_citation_of_a_nonexistent_evidence_id() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(
        ModelNarration(
            text="Confirmed by team radio and by telemetry showing a lock-up.",
            cited_evidence_ids=(radio.evidence_id, "telemetry:made_up"),
            no_clear_cause=False,
        )
    )

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.text == FALLBACK_TEXT
    assert not explanation.grounded
    assert explanation.fallback_reason is not None
    assert "telemetry:made_up" in explanation.fallback_reason


def test_grounding_validator_rejects_an_explanation_with_no_citations() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(
        ModelNarration(
            text="It was probably a mistake.", cited_evidence_ids=(), no_clear_cause=False
        )
    )

    explanation = explain(bundle, model, NarrationParams())

    assert explanation.text == FALLBACK_TEXT
    assert not explanation.grounded
    assert explanation.fallback_reason == "explanation given with no cited evidence"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_explain_is_deterministic_given_a_fixed_mock() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(
        ModelNarration(
            text="Confirmed by team radio.",
            cited_evidence_ids=(radio.evidence_id,),
            no_clear_cause=False,
        )
    )

    first = explain(bundle, model, NarrationParams())
    second = explain(bundle, model, NarrationParams())

    assert first == second


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------


def test_explain_cached_never_calls_the_model_twice_for_the_same_anomaly() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    model = MockNarrationModel(
        ModelNarration(
            text="Confirmed by team radio.",
            cited_evidence_ids=(radio.evidence_id,),
            no_clear_cause=False,
        )
    )
    store = MemoryExplanationStore()

    first = explain_cached(bundle, model, store, NarrationParams())
    second = explain_cached(bundle, model, store, NarrationParams())

    assert first == second
    assert len(model.calls) == 1
    assert store.get(bundle.anomaly_id) == first


def test_explain_cached_returns_a_stored_explanation_without_ever_building_a_bundle() -> None:
    """A cache hit must not even need the model — proves the cache is
    actually consulted first, not just written to."""
    store = MemoryExplanationStore()
    cached = Explanation(
        anomaly_id="pace_loss:8:44:123",
        text="cached answer",
        cited_evidence_ids=(),
        confidence=ExplanationConfidence.HIGH,
        grounded=True,
        fallback_reason=None,
    )
    store.set("pace_loss:8:44:123", cached)
    bundle = _bundle((_ANOMALY_FACT,), low_evidence=True)  # would otherwise short-circuit anyway
    model = MockNarrationModel(ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True))

    result = explain_cached(bundle, model, store, NarrationParams())

    assert result == cached
    assert model.calls == []


def test_an_outage_is_never_cached() -> None:
    """ "The narration service did not respond" is a fact about one request.
    Cached, it froze every affected moment of a race at that text until the
    process restarted, and the real explanation was never asked for again."""
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)
    outage = MockNarrationModel(
        ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True, provider_failed=True)
    )
    store = MemoryExplanationStore()

    first = explain_cached(bundle, outage, store, NarrationParams())
    assert first.fallback_reason == "provider_unavailable"
    assert store.get(bundle.anomaly_id) is None

    recovered = MockNarrationModel(
        ModelNarration(
            text="Locked up.", cited_evidence_ids=("radio_clip:44:t1",), no_clear_cause=False
        )
    )
    second = explain_cached(bundle, recovered, store, NarrationParams())
    assert second.grounded
    assert recovered.calls, "the next request must actually ask the model"


# ---------------------------------------------------------------------------
# build_prompt: pure and deterministic
# ---------------------------------------------------------------------------


def test_build_prompt_cites_every_evidence_id_and_is_deterministic() -> None:
    radio = _item(EvidenceKind.RADIO_CLIP, "radio_clip:44:t1", 0.9)
    bundle = _bundle((_ANOMALY_FACT, radio), low_evidence=False)

    prompt = build_prompt(bundle)

    assert "id=anomaly |" in prompt
    assert "id=radio_clip:44:t1 |" in prompt
    assert "Valid ids to cite: anomaly, radio_clip:44:t1" in prompt
    assert prompt == build_prompt(bundle)


def test_build_prompt_never_puts_a_kind_where_an_id_is_read() -> None:
    """Ling cited `anomaly_fact` for the item whose id is `anomaly`, every
    time: the old `[anomaly] (anomaly_fact, ...)` layout put the kind right
    where a reader expects the id, and the grounding validator correctly
    discarded every such answer. The kind is now rendered in words."""
    bundle = _bundle((_ANOMALY_FACT,), low_evidence=False)

    prompt = build_prompt(bundle)

    assert "id=anomaly | anomaly fact |" in prompt
    assert "(anomaly_fact" not in prompt
    assert "[anomaly]" not in prompt


def test_build_prompt_names_the_drivers_it_was_given() -> None:
    """A sentence should read "Norris", not "driver 4". The live feed sends
    surnames in capitals; the prompt normalises them."""
    bundle = replace(
        _bundle((_ANOMALY_FACT,), low_evidence=False),
        roster=(DriverRef(number="4", code="NOR", full_name="Lando NORRIS", team="McLaren"),),
    )

    prompt = build_prompt(bundle)

    assert "#4 = NOR, Lando Norris (McLaren)" in prompt
    assert "refer to them by surname" in prompt
