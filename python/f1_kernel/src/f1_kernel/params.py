"""`KernelParams`: every tunable constant in the kernel, in one typed place.

CLAUDE.md's Phase 2 invariant: *no hardcoded magic numbers*. Fuel
coefficient, pit loss, dirty-air threshold, compound offsets — all exposed
as typed, overridable parameters with documented defaults and provenance.
Every analytic takes a `KernelParams`; nothing reads a global constant.

**Provenance is tracked per field** (`PARAM_PROVENANCE`). Most fields are
still `PROVISIONAL`: a plausible published/industry figure or a round
number chosen to be roughly right, NOT something this project has measured.
Later Phase 2 prompts calibrate them, one analytic at a time, against the
2023-2025 backfill and flip the provenance to `CALIBRATED` with the fit
that produced the number, documented in `docs/PHASE2.md` (so far:
`fuel_coef_s_per_kg`, `compound_offset_s`). Until a field says
`CALIBRATED`, treat its absolute output as order-of-magnitude and any
*relative* comparison (same params both sides) as the trustworthy part.

Overriding is by `with_overrides` (or plain `model_copy(update=...)`);
params are frozen so an analytic can never mutate the caller's copy:

    params = KernelParams()
    monza = params.with_overrides(dirty_air_threshold_s=1.0)

Round-tripping through JSON is exact (`model_validate_json(
params.model_dump_json()) == params`), so a backtest can record the exact
parameter set that produced a result.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from f1_domain import Compound


class ParamProvenance(StrEnum):
    """Where a default came from — the honesty marker on every constant."""

    PROVISIONAL = "provisional"
    """A plausible round number or published figure, NOT measured by us.
    Calibrate in a later prompt before trusting an absolute output."""

    CALIBRATED = "calibrated"
    """Fitted from our own historical backfill; the fit is documented in
    `docs/PHASE2.md` alongside the season(s) and sessions it used."""

    STRUCTURAL = "structural"
    """Not an empirical quantity at all — a definitional threshold or a
    sample-size floor. There is nothing to calibrate; it is a policy choice."""


class KernelParams(BaseModel):
    """The full parameter set. Frozen, JSON round-trippable, and the single
    argument every analytic takes besides its frame."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- fuel correction (Phase 2.3) --------------------------------------
    fuel_coef_s_per_kg: float = Field(
        default=0.0456,
        gt=0,
        description=(
            "Lap-time penalty per kilogram of fuel carried, in seconds. "
            "CALIBRATED (see docs/PHASE2.md 'Fuel coefficient calibration'): "
            "0.0456 s/kg, 95% CI [0.031, 0.060], from a cross-circuit "
            "regression over 780 hard/medium-compound stints across 23 "
            "2024 races (`python/f1_kernel/tools/calibrate_fuel.py`, output "
            "committed at `f1_kernel/calibration/fuel_coefficient.json`). "
            "Still a single season-wide figure, not per-circuit — a long, "
            "heavy-braking lap plausibly punishes weight more than a short, "
            "flowing one, which this does not yet capture."
        ),
    )
    fuel_burn_kg_per_lap: float = Field(
        default=1.80,
        gt=0,
        description=(
            "Fuel burned per racing lap, in kilograms. Roughly 110 kg over a "
            "~57-lap race; varies with circuit length and engine mode."
        ),
    )
    start_fuel_kg: float = Field(
        default=110.0,
        gt=0,
        description=(
            "Fuel load at the race start, in kilograms — the regulation "
            "maximum. Only meaningful for races; practice/qualifying loads "
            "are unknown and fuel correction should not be applied there."
        ),
    )
    fuel_reference_kg: float = Field(
        default=0.0,
        ge=0,
        description=(
            "The common fuel load `fuel_correct` normalizes every lap to. "
            "Zero (an empty tank) is the natural reference: it needs no "
            "assumption about any particular race's start load, and every "
            "corrected time reads as 'how fast would this lap have been "
            "with no fuel effect at all'. A policy choice, not a measured "
            "quantity — any fixed reference gives the same RELATIVE "
            "comparisons, since it only shifts every lap by a constant."
        ),
    )

    # -- dirty air (Phase 2.2) --------------------------------------------
    dirty_air_threshold_s: float = Field(
        default=1.2,
        gt=0,
        description=(
            "Interval to the car ahead, in seconds, below which a lap counts "
            "as run in dirty air. ~1.0s is where meaningful downforce loss "
            "starts on most 2022+ cars; 1.2s is deliberately slightly "
            "conservative (excludes a few extra laps rather than polluting "
            "a degradation fit with them)."
        ),
    )
    dirty_air_min_samples: int = Field(
        default=3,
        ge=1,
        description=(
            "Minimum number of interval samples inside a lap window before "
            "the dirty-air verdict is considered known. Below this the lap "
            "is marked dirty-air-UNKNOWN and is not excluded — see "
            "`f1_kernel.cleaning`."
        ),
    )

    # -- robust outlier bound (Phase 2.2) ---------------------------------
    outlier_mad_multiplier: float = Field(
        default=3.5,
        gt=0,
        description=(
            "How many scaled-MAD units from the stint median a lap time may "
            "sit before it is anomalous. 3.5 on the 1.4826-scaled MAD is "
            "~3.5 sigma for normally distributed times, but unlike sigma it "
            "does not move when one spin is in the sample."
        ),
    )
    outlier_floor_s: float = Field(
        default=0.40,
        gt=0,
        description=(
            "Absolute floor on the outlier bound, in seconds. A very "
            "consistent stint can have a near-zero MAD, which would flag "
            "every ordinary tenth as an anomaly; the bound never tightens "
            "below this."
        ),
    )
    outlier_min_laps: int = Field(
        default=5,
        ge=3,
        description=(
            "Minimum candidate laps in a stint before a robust bound is "
            "estimated at all. Below this the median/MAD are not "
            "meaningful and no lap in the group is flagged anomalous."
        ),
    )

    # -- pit loss (Phase 2.5) ---------------------------------------------
    default_pit_loss_s: float = Field(
        default=23.03,
        gt=0,
        description=(
            "Total time lost to a green-flag pit stop versus staying out, in "
            "seconds, when the circuit is unknown — INCLUDING the average "
            "out-lap warm-up cost (see `f1_kernel.pit_loss` for why the two "
            "cannot be separated from lap times). CALIBRATED (see "
            "docs/PHASE2.md 'Pit-loss calibration'): the cross-circuit median "
            "of 19 per-circuit medians over 410 green-flag stops across 23 "
            "2024 races (`python/f1_kernel/tools/calibrate_pit_loss.py`, "
            "output committed at `f1_kernel/calibration/pit_loss.json`; "
            "per-circuit spread 18.8s Spa to 30.1s Singapore, so prefer the "
            "circuit file's own value whenever one ships). Per-circuit "
            "values in the circuit files and `pit_loss_overrides_s` both "
            "override this."
        ),
    )
    pit_loss_overrides_s: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Per-circuit pit-loss override, keyed by `circuit_id`. Wins over "
            "both the circuit file and `default_pit_loss_s` — the escape "
            "hatch for a one-off (pit-lane resurfacing, a new speed limit) "
            "without editing checked-in circuit data."
        ),
    )
    pit_stationary_s: float = Field(
        default=2.6,
        gt=0,
        description=(
            "Stationary time for a routine tyre change, in seconds. Part of "
            "`pit_loss` already; kept separately so undercut modelling can "
            "reason about a slow stop."
        ),
    )

    # -- undercut / overcut crossover (Phase 2.5) -------------------------
    undercut_out_lap_penalty_s: float = Field(
        default=0.0,
        ge=0,
        description=(
            "EXTRA out-lap cost on top of `pit_loss_s`, in seconds — fresh "
            "tyres taking a lap to switch on. Zero by default because the "
            "calibrated per-circuit pit loss is measured as the TOTAL "
            "(in-lap + out-lap) delta against clean neighbouring laps, which "
            "already contains the average warm-up cost (lap times alone "
            "cannot separate pit-lane transit from warm-up — see "
            "`f1_kernel.pit_loss`). Set it above zero only when supplying a "
            "bare pit-lane transit figure via `pit_loss_overrides_s`, or to "
            "model unusually slow warm-up (cold track, wets). Note that in "
            "the crossover margin itself an equal per-stop cost CANCELS "
            "between the two cars (see `f1_kernel.undercut`); this mostly "
            "affects the projected rejoin gap."
        ),
    )
    undercut_horizon_laps: int = Field(
        default=25,
        ge=2,
        description=(
            "How many laps past the decision lap the crossover projection "
            "scans for the defender's eventual stop (also the cap on the "
            "window-opening scan). A defender more laps than this from "
            "stopping is outside any realistic undercut duel; reported as "
            "'does not come through within the horizon' rather than "
            "extrapolating a linear tyre model tens of laps out."
        ),
    )
    undercut_max_gap_s: float = Field(
        default=30.0,
        gt=0,
        description=(
            "Largest on-track gap, in seconds, at which a pair of cars still "
            "counts as an undercut/overcut duel for the pairwise snapshot and "
            "the backtest harness. Beyond roughly a pit loss and a half the "
            "cars are not racing each other on strategy; `undercut_window` "
            "itself accepts any gap when called directly."
        ),
    )

    rejoin_high_margin_sigmas: float = Field(
        default=2.0,
        gt=0,
        description=(
            "A rejoin call (`f1_kernel.rejoin`) is high confidence when the nearest "
            "car is at least this many single-stop spreads away: a stop this far off "
            "its median is rare enough (about 1 in 20 under a normal spread) to call. "
            "Expressed in the circuit's own measured spread, so nothing is in seconds."
        ),
    )
    rejoin_medium_margin_sigmas: float = Field(
        default=1.0,
        gt=0,
        description=(
            "...and medium when it is at least this many; closer is low. Validated, "
            "not fitted: the rejoin backtest must show high beating medium beating low."
        ),
    )

    # -- degradation fitting (Phase 2.3) ----------------------------------
    min_laps_for_degradation: int = Field(
        default=5,
        ge=2,
        description=(
            "Minimum clean laps in a stint before a degradation fit counts "
            "as `good` rather than `thin`. Fewer laps still produce a fit "
            "on request, but `DegradationFit.reliability` reports it as "
            "thin — never presented as equal to a fit from a full stint."
        ),
    )
    degradation_max_ci_width_s_per_lap: float = Field(
        default=0.30,
        gt=0,
        description=(
            "Above this width (seconds/lap), the 95% confidence interval on "
            "a degradation rate is too wide to trust even with plenty of "
            "laps — a rate that could be anywhere from -0.1 to +0.2 s/lap "
            "is not evidence of anything. Gates `good` vs `unreliable` once "
            "a fit already has enough laps to clear the `thin` floor."
        ),
    )
    degradation_quadratic_min_laps: int = Field(
        default=8,
        ge=4,
        description=(
            "Minimum clean laps in a stint before the optional quadratic "
            "('falls off a cliff') degradation term is attempted at all. "
            "Below this a curvature estimate is just noise; "
            "`DegradationFit.quadratic_term_s_per_lap2` stays None."
        ),
    )
    degradation_huber_scale_s: float = Field(
        default=0.5,
        gt=0,
        description=(
            "Huber-loss transition scale (seconds) for the optional robust "
            "quadratic degradation fit: residuals beyond this are "
            "down-weighted rather than allowed to dominate the curvature "
            "estimate, the same role `outlier_floor_s` plays for the "
            "clean-lap filter's MAD bound."
        ),
    )
    compound_offset_s: dict[Compound, float] = Field(
        default_factory=lambda: {
            Compound.SOFT: 0.0,
            Compound.MEDIUM: 0.45,
            Compound.HARD: 0.36,
            Compound.INTERMEDIATE: 6.0,
            Compound.WET: 12.0,
        },
        description=(
            "Baseline lap-time offset per compound versus the soft, in "
            "seconds, on a dry track. PARTIALLY CALIBRATED (see "
            "docs/PHASE2.md 'Compound offset calibration' and "
            "`f1_kernel.degradation.calibrate_compound_offsets`): HARD is "
            "the median paired driver-session delta from 79 samples across "
            "13 2024 races (`python/f1_kernel/tools/calibrate_compound_offsets.py`, "
            "output committed at `f1_kernel/calibration/compound_offsets.json`). "
            "MEDIUM stays at its earlier placeholder — the same calibration "
            "run fits it as slightly NEGATIVE (medium measured faster than "
            "soft), which the method's own docstring identifies as a known "
            "confound (session-level drift that does not cancel between a "
            "driver's stints), not a real effect; shipping a number known to "
            "be implausible would be worse than leaving it provisional. Real "
            "offsets also depend on the specific C1-C5 allocation for the "
            "weekend, so treat even the calibrated figure as a season-wide "
            "average. Wet/intermediate are not calibrated at all — comparing "
            "them to a dry reference compound measures the wet-vs-dry gap, "
            "not a compound effect — and exist only so lookups never raise a "
            "KeyError."
        ),
    )

    # -- braking-point drift (Phase 2.6) ----------------------------------
    brake_on_threshold_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        description=(
            "Brake-channel percentage above which the driver counts as "
            "braking. Some seasons send brake as effectively boolean "
            "(0/100), so any threshold in the middle behaves identically "
            "there; the threshold matters only for analogue seasons."
        ),
    )
    braking_distance_bin_m: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Distance-bin width, in metres, for resampling telemetry onto a "
            "common per-lap distance grid before comparing braking points."
        ),
    )
    braking_approach_window_m: float = Field(
        default=250.0,
        gt=0,
        description=(
            "How far before a corner, in lap-distance metres, "
            "`braking_points` searches for the sustained brake-application "
            "point. STRUCTURAL: a search-window cap, not a per-circuit "
            "measured quantity — long enough to cover essentially every "
            "real F1 braking zone (even the longest, into a first-gear "
            "hairpin off a DRS straight, runs to roughly 200m) while "
            "stopping the search from wandering into the previous corner's "
            "own exit. Clipped further at the previous corner's own "
            "distance when two corners sit closer together than this "
            "(a chicane)."
        ),
    )
    braking_min_sustained_m: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Minimum distance, in metres, the brake trace must stay above "
            "`brake_on_threshold_pct` before a detection counts as genuine "
            "braking rather than a single noisy stab or a lift-and-coast. "
            "STRUCTURAL: a debounce floor, not a measured quantity."
        ),
    )
    braking_min_samples_in_window: int = Field(
        default=3,
        ge=1,
        description=(
            "Minimum telemetry samples inside a corner's approach window "
            "before a braking-point detection counts as KNOWN. Below this, "
            "`braking_points` reports the corner/lap as unknown rather than "
            "risking a detection built on one or two samples — the same "
            "'unknown is not excluded, unknown is reported' rule "
            "`dirty_air_min_samples` follows for the clean-lap filter."
        ),
    )
    min_laps_for_braking_drift: int = Field(
        default=5,
        ge=2,
        description=(
            "Minimum known braking points in a (driver, stint, corner) "
            "group before `braking_drift`'s fit counts as `good` rather "
            "than `thin` — the same role `min_laps_for_degradation` plays "
            "for tyre degradation."
        ),
    )
    braking_drift_max_ci_width_m_per_lap: float = Field(
        default=8.0,
        gt=0,
        description=(
            "Above this width (metres/lap), the 95% confidence interval on "
            "a braking-drift rate is too wide to trust even with plenty of "
            "laps. Wider than degradation's equivalent gate because a "
            "brake point is inherently coarser than a lap time — it is "
            "pinned to individual telemetry samples spaced several metres "
            "apart at corner-approach speed, not a photocell. STRUCTURAL: "
            "a definitional gate, not yet calibrated against a measured "
            "spread."
        ),
    )

    def with_overrides(self, **overrides: object) -> KernelParams:
        """A copy with `overrides` applied, re-validated. Raises on an
        unknown parameter name or an out-of-range value — a typo in an
        override must never silently do nothing."""
        return KernelParams.model_validate({**self.model_dump(), **overrides})

    def pit_loss_for(
        self, circuit_id: str | None, circuit_pit_loss_s: float | None = None
    ) -> float:
        """Pit loss for a circuit, resolving the precedence chain:
        `pit_loss_overrides_s` > the circuit file's value > `default_pit_loss_s`."""
        if circuit_id is not None and circuit_id in self.pit_loss_overrides_s:
            return self.pit_loss_overrides_s[circuit_id]
        if circuit_pit_loss_s is not None:
            return circuit_pit_loss_s
        return self.default_pit_loss_s

    def compound_offset(self, compound: Compound | str | None) -> float:
        """Baseline offset for a compound, 0.0 for unknown/missing — an
        unknown compound must not silently shift a lap time."""
        if compound is None:
            return 0.0
        try:
            key = Compound(compound)
        except ValueError:
            return 0.0
        return self.compound_offset_s.get(key, 0.0)


PARAM_PROVENANCE: dict[str, ParamProvenance] = {
    "fuel_coef_s_per_kg": ParamProvenance.CALIBRATED,
    "fuel_burn_kg_per_lap": ParamProvenance.PROVISIONAL,
    "start_fuel_kg": ParamProvenance.PROVISIONAL,
    "fuel_reference_kg": ParamProvenance.STRUCTURAL,
    "dirty_air_threshold_s": ParamProvenance.PROVISIONAL,
    "dirty_air_min_samples": ParamProvenance.STRUCTURAL,
    "outlier_mad_multiplier": ParamProvenance.STRUCTURAL,
    "outlier_floor_s": ParamProvenance.PROVISIONAL,
    "outlier_min_laps": ParamProvenance.STRUCTURAL,
    "default_pit_loss_s": ParamProvenance.CALIBRATED,
    "pit_loss_overrides_s": ParamProvenance.STRUCTURAL,
    "pit_stationary_s": ParamProvenance.PROVISIONAL,
    "undercut_out_lap_penalty_s": ParamProvenance.PROVISIONAL,
    "undercut_horizon_laps": ParamProvenance.STRUCTURAL,
    "undercut_max_gap_s": ParamProvenance.STRUCTURAL,
    "rejoin_high_margin_sigmas": ParamProvenance.STRUCTURAL,
    "rejoin_medium_margin_sigmas": ParamProvenance.STRUCTURAL,
    "min_laps_for_degradation": ParamProvenance.STRUCTURAL,
    "degradation_max_ci_width_s_per_lap": ParamProvenance.STRUCTURAL,
    "degradation_quadratic_min_laps": ParamProvenance.STRUCTURAL,
    "degradation_huber_scale_s": ParamProvenance.STRUCTURAL,
    "compound_offset_s": ParamProvenance.CALIBRATED,
    "brake_on_threshold_pct": ParamProvenance.PROVISIONAL,
    "braking_distance_bin_m": ParamProvenance.STRUCTURAL,
    "braking_approach_window_m": ParamProvenance.STRUCTURAL,
    "braking_min_sustained_m": ParamProvenance.STRUCTURAL,
    "braking_min_samples_in_window": ParamProvenance.STRUCTURAL,
    "min_laps_for_braking_drift": ParamProvenance.STRUCTURAL,
    "braking_drift_max_ci_width_m_per_lap": ParamProvenance.STRUCTURAL,
}
"""Field name -> where its default came from. A test asserts this covers
every field of `KernelParams`, so a new parameter cannot be added without
stating its provenance."""


def describe_params(params: KernelParams | None = None) -> list[tuple[str, object, str, str]]:
    """`(name, value, provenance, description)` for every parameter — what
    a UI or a backtest report shows so a number is never anonymous."""
    resolved = params if params is not None else KernelParams()
    rows: list[tuple[str, object, str, str]] = []
    for name, field in KernelParams.model_fields.items():
        rows.append(
            (
                name,
                getattr(resolved, name),
                PARAM_PROVENANCE[name].value,
                field.description or "",
            )
        )
    return rows
