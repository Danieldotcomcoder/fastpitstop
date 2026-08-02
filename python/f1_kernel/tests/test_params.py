"""`KernelParams`: round-tripping, overriding, and the discipline that
every constant states where it came from."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from f1_domain import Compound
from f1_kernel import KernelParams, describe_params
from f1_kernel.params import PARAM_PROVENANCE, ParamProvenance


def test_params_round_trip_through_json() -> None:
    """A backtest result is only reproducible if the exact parameter set can
    be stored and read back."""
    params = KernelParams()

    restored = KernelParams.model_validate_json(params.model_dump_json())

    assert restored == params
    assert restored.compound_offset_s == params.compound_offset_s


def test_customized_params_round_trip() -> None:
    params = KernelParams().with_overrides(
        dirty_air_threshold_s=0.9,
        pit_loss_overrides_s={"monza": 26.5},
    )

    restored = KernelParams.model_validate_json(params.model_dump_json())

    assert restored == params
    assert restored.pit_loss_overrides_s == {"monza": 26.5}


def test_overriding_a_param_changes_only_that_param() -> None:
    base = KernelParams()

    overridden = base.with_overrides(fuel_coef_s_per_kg=0.028)

    assert overridden.fuel_coef_s_per_kg == 0.028
    assert base.fuel_coef_s_per_kg == KernelParams().fuel_coef_s_per_kg, (
        "the original must not be mutated"
    )
    assert overridden.model_dump(exclude={"fuel_coef_s_per_kg"}) == base.model_dump(
        exclude={"fuel_coef_s_per_kg"}
    )


def test_params_are_frozen() -> None:
    params = KernelParams()

    with pytest.raises(ValidationError):
        params.fuel_coef_s_per_kg = 0.05  # type: ignore[misc]


def test_a_typo_in_an_override_is_rejected() -> None:
    """Silently ignoring an unknown parameter would mean a calibration run
    that quietly did nothing."""
    with pytest.raises(ValidationError):
        KernelParams().with_overrides(dirty_air_threshhold_s=1.0)


def test_out_of_range_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        KernelParams().with_overrides(fuel_coef_s_per_kg=-1.0)
    with pytest.raises(ValidationError):
        KernelParams().with_overrides(brake_on_threshold_pct=150.0)


def test_pit_loss_precedence() -> None:
    """override > circuit file > default."""
    params = KernelParams().with_overrides(pit_loss_overrides_s={"monza": 26.5})

    assert params.pit_loss_for("monza", 24.0) == 26.5
    assert params.pit_loss_for("bahrain", 21.5) == 21.5
    assert params.pit_loss_for("bahrain", None) == params.default_pit_loss_s
    assert params.pit_loss_for(None, None) == params.default_pit_loss_s


def test_compound_offset_lookup_is_total() -> None:
    """An unknown or missing compound must not silently shift a lap time."""
    params = KernelParams()

    assert params.compound_offset(Compound.SOFT) == 0.0
    assert params.compound_offset("medium") == 0.45
    assert params.compound_offset(None) == 0.0
    assert params.compound_offset(Compound.UNKNOWN) == 0.0
    assert params.compound_offset("not-a-compound") == 0.0


def test_every_param_declares_its_provenance() -> None:
    """The rule that keeps a magic number from sneaking back in: a new
    parameter cannot be added without saying where its default came from."""
    fields = set(KernelParams.model_fields)

    assert set(PARAM_PROVENANCE) == fields


def test_every_param_is_documented() -> None:
    fields = KernelParams.model_fields

    undocumented = [name for name, field in fields.items() if not field.description]
    assert undocumented == []


CALIBRATED_PARAMS = frozenset({"fuel_coef_s_per_kg", "compound_offset_s", "default_pit_loss_s"})
"""Empirical parameters that HAVE been fit against the 2023-2025 backfill,
with the fit documented in docs/PHASE2.md. Everything else empirical must
still be provisional — see the test below."""


def test_calibrated_params_are_marked_as_such() -> None:
    for name in CALIBRATED_PARAMS:
        assert PARAM_PROVENANCE[name] is ParamProvenance.CALIBRATED, name


def test_most_defaults_are_still_provisional() -> None:
    """A guard against false confidence: a parameter cannot claim to be
    calibrated unless it is actually in `CALIBRATED_PARAMS` (and therefore
    documented in docs/PHASE2.md) — everything else empirical stays
    provisional until its own calibration prompt runs."""
    empirical = [
        name
        for name, provenance in PARAM_PROVENANCE.items()
        if provenance is not ParamProvenance.STRUCTURAL
    ]
    still_provisional = [name for name in empirical if name not in CALIBRATED_PARAMS]

    assert empirical, "expected at least some empirical parameters"
    assert still_provisional, "expected at least some parameters still awaiting calibration"
    assert all(
        PARAM_PROVENANCE[name] is ParamProvenance.PROVISIONAL for name in still_provisional
    ), "a parameter claims to be calibrated without being documented in docs/PHASE2.md"


def test_describe_params_reports_value_provenance_and_docs() -> None:
    rows = describe_params(KernelParams().with_overrides(dirty_air_threshold_s=0.8))

    described = {name: (value, provenance, doc) for name, value, provenance, doc in rows}
    assert len(described) == len(KernelParams.model_fields)
    value, provenance, doc = described["dirty_air_threshold_s"]
    assert value == 0.8
    assert provenance == ParamProvenance.PROVISIONAL.value
    assert "dirty air" in doc
