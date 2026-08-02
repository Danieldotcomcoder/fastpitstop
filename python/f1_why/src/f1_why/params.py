"""`AnomalyParams`: every tunable constant the detector uses, in one typed
place — the same "no hardcoded magic numbers" discipline `f1_kernel.params.
KernelParams` applies to the analytics kernel. Detection-specific tunables
only; anything that is really a kernel question (the fuel coefficient, the
clean-lap outlier bound, the calibrated pit loss) stays in the
`KernelParams` already carried on `KernelReport.params` and is reused from
there rather than duplicated here.

Every default below is a deliberately chosen round number, not fitted from
data — there is no calibration corpus for "how big a pace loss is worth
explaining" yet. Treat them as a starting point to override, not a
measured quantity, the same honesty `KernelParams`' own PROVISIONAL fields
carry until a calibration prompt fits them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AnomalyParams(BaseModel):
    """Frozen, JSON round-trippable, the single argument (besides frames and
    a `KernelReport`) `detect_anomalies` takes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- pace loss / off-track (shared robust-outlier model) --------------
    outlier_mad_multiplier: float = Field(
        default=3.0,
        gt=0,
        description=(
            "How many scaled-MAD units a lap's (pace_loss) or one mini-"
            "sector's (off_track) fuel-corrected/raw time may sit above its "
            "own TRAILING clean-lap window's median before it counts as an "
            "anomaly. Deliberately independent of "
            "KernelParams.outlier_mad_multiplier (the clean-lap filter's own "
            "bound): that one decides whether a lap should be excluded from "
            "fitting; this one decides whether a lap is worth explaining to "
            "a user, a different question that should not silently move if "
            "the fitting bound is retuned."
        ),
    )
    outlier_floor_s: float = Field(
        default=0.6,
        gt=0,
        description=(
            "Absolute floor on the outlier bound, in seconds, so a very "
            "consistent trailing window (near-zero MAD) does not flag "
            "ordinary tenths."
        ),
    )
    lookback_laps: int = Field(
        default=6,
        ge=2,
        description=(
            "How many of the most recent CLEAN laps strictly before a "
            "candidate, within the same (driver, stint), form its baseline "
            "window. A TRAILING window rather than the whole stint's median "
            "is deliberate: a stint with genuine (non-anomalous) "
            "degradation curvature drifts slower over its own length, and "
            "comparing a late lap against the whole stint's average would "
            "flag ordinary late-stint pace as a false pace_loss. Comparing "
            "against only the RECENT trend — literally what CLAUDE.md's "
            "Phase 5 prompt asks for — tracks that drift instead of "
            "fighting it."
        ),
    )
    min_reference_laps: int = Field(
        default=4,
        ge=2,
        description=(
            "Minimum clean laps inside the trailing window before a robust "
            "median/MAD baseline is trusted at all. Below this, pace_loss "
            "and off_track make no call for that lap rather than estimating "
            "a bound from almost nothing (e.g. the first few laps of a "
            "stint, or right after a period with few clean laps)."
        ),
    )

    # -- position change ----------------------------------------------------
    position_change_seconds_per_place: float = Field(
        default=1.5,
        gt=0,
        description=(
            "Notional seconds-of-significance one on-track place change is "
            "worth, before the front-of-field weighting below — the unit "
            "salience uses to compare a position change against a pace loss "
            "or a pit anomaly on one common scale."
        ),
    )
    position_change_lead_bonus: float = Field(
        default=5.0,
        ge=0,
        description=(
            "Extra weight for a swap happening near the front: the "
            "seconds-per-place value above is scaled by "
            "`1 + lead_bonus / max(position, 1)`, so a P1/P2 fight reads far "
            "more salient than a P18/P19 one of the same size."
        ),
    )

    # -- pit anomaly ----------------------------------------------------------
    pit_anomaly_min_excess_s: float = Field(
        default=3.0,
        gt=0,
        description=(
            "Minimum absolute difference, in seconds, between a green-flag "
            "stop's measured cost (`f1_kernel.pit_loss.pit_loss_samples`) "
            "and the circuit's own calibrated/default pit loss before it "
            "counts as anomalous. A fixed floor rather than a robust bound: "
            "one race rarely has enough stops to estimate its own spread, "
            "and the calibrated norm is already a robust cross-race figure."
        ),
    )

    # -- undercut resolved --------------------------------------------------
    undercut_resolved_confidence_weight: dict[str, float] = Field(
        default_factory=lambda: {"high": 1.0, "medium": 0.7, "low": 0.4},
        description=(
            "Salience multiplier per `f1_kernel.undercut.Confidence` value "
            "the original call carried — a coin-flip LOW-confidence call "
            "resolving either way is less noteworthy than a well-supported "
            "HIGH-confidence one doing the same."
        ),
    )
    undercut_resolved_upset_multiplier: float = Field(
        default=1.6,
        ge=1.0,
        description=(
            "Extra salience multiplier when a HIGH-confidence call was "
            "WRONG (predicted_ahead != actual_ahead) — a genuine upset is "
            "more worth explaining than a routine confirmation."
        ),
    )

    # -- degradation cliff --------------------------------------------------
    deg_cliff_min_quadratic_term_s_per_lap2: float = Field(
        default=0.01,
        gt=0,
        description=(
            "Minimum positive `DegradationFit.quadratic_term_s_per_lap2` "
            "before a stint counts as a cliff at all. `fit_degradation` "
            "only attempts the quadratic term with "
            "`KernelParams.degradation_quadratic_min_laps`+ laps, so this "
            "floor is the only extra gate needed here."
        ),
    )
    deg_cliff_min_extra_time_s: float = Field(
        default=1.5,
        ge=0,
        description=(
            "Minimum implied extra time, in seconds, the quadratic term "
            "must cost at the last lap the fit used (versus the pure linear "
            "extrapolation) before it is worth surfacing."
        ),
    )

    # -- salience -------------------------------------------------------------
    salience_scale_s: float = Field(
        default=6.0,
        gt=0,
        description=(
            "The seconds-equivalent magnitude at which salience reaches "
            "`1 - 1/e` (~0.63) under `salience = 1 - exp(-x / scale)`. A "
            "smaller scale makes small events read as more salient; this is "
            "the one knob that sets the whole ranking's sensitivity."
        ),
    )
    unreliable_fit_derate: float = Field(
        default=0.5,
        gt=0,
        le=1.0,
        description=(
            "Salience multiplier applied when the evidence behind an "
            "anomaly rests on an UNRELIABLE degradation fit — the honesty-"
            "propagation CLAUDE.md's Phase 5 invariants ask for: a shaky fit "
            "should rank lower, never vanish outright (the caveat still "
            "rides along in `Anomaly.reasons`)."
        ),
    )

    def with_overrides(self, **overrides: object) -> AnomalyParams:
        """A copy with `overrides` applied, re-validated. Raises on an
        unknown parameter name or an out-of-range value, matching
        `KernelParams.with_overrides`."""
        return AnomalyParams.model_validate({**self.model_dump(), **overrides})


__all__ = ["AnomalyParams"]
