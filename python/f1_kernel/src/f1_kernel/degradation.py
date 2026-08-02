"""Tyre degradation fitting: how much a stint slows down per lap of wear,
once fuel is out of the picture.

Public strategy tools mostly fake this with a look at the timing screen and
a gut feel ("mediums are going off now"). This module measures it, per
(driver, stint, compound), from the fuel-corrected lap frame `f1_kernel.fuel`
produces:

- **`fit_degradation`** — a robust (Theil-Sen) linear fit of fuel-corrected
  lap time against `tyre_age` within each stint, with an optional Huber
  quadratic term for compounds that fall off a cliff rather than degrading
  linearly. Theil-Sen is the point of the exercise: an OLS fit lets one
  missed apex or one lap of traffic drag the whole rate towards it, and a
  single bad lap is exactly the kind of thing that happens constantly in
  real stints. Every fit reports its own confidence interval and a
  `reliability` verdict, so a 4-lap fit is never displayed as equal to a
  15-lap one.
- **`calibrate_compound_offsets`** — the one-time (but re-runnable) offline
  routine that estimates each compound's baseline pace offset from
  history — see `python/f1_kernel/tools/calibrate_compound_offsets.py`,
  whose OUTPUT is committed into `KernelParams.compound_offset_s`.

This module does not compute undercut windows or pit strategy — it stops at
the fitted rate and the calibrated offsets.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import polars as pl
from scipy.optimize import least_squares  # type: ignore[reportUnknownVariableType]

from f1_domain import Compound, SessionKey
from f1_kernel._robust_stats import theilslopes as _theilslopes
from f1_kernel.params import KernelParams
from f1_kernel.schemas import Schema
from f1_kernel.sources import EventSource

_REQUIRED_COLUMNS = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "stint_number",
    "compound",
    "tyre_age",
    "lap_time_fuel_corrected_s",
)

_GROUP_COLUMNS = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "stint_number",
    "compound",
)

DEFAULT_REFERENCE_COMPOUND = Compound.SOFT
"""The compound every calibrated offset is expressed relative to (0.0 by
definition) — the fastest dry compound in a normal allocation, matching
`KernelParams.compound_offset_s`'s existing convention."""

DRY_COMPOUNDS: frozenset[Compound] = frozenset({Compound.SOFT, Compound.MEDIUM, Compound.HARD})
"""The only compounds `calibrate_compound_offsets` will pair against each
other. `KernelParams.compound_offset_s` itself documents its values as
"on a dry track" — pairing a wet-weather intermediate/wet stint against a
dry soft stint would not measure a compound effect at all, it would measure
the (enormous, and totally different) wet-vs-dry pace gap. Intermediate and
wet stay whatever `KernelParams` already has for them; this calibration
does not touch those two entries."""


class Reliability(StrEnum):
    """How much a `DegradationFit` should be trusted — driven by lap count
    and fit quality, never presented as a single undifferentiated number."""

    GOOD = "good"
    """Enough laps (`KernelParams.min_laps_for_degradation`) and a tight
    enough confidence interval (`degradation_max_ci_width_s_per_lap`)."""

    THIN = "thin"
    """Below the minimum lap count. Still a real fit — just not one to
    weight as heavily as a full stint."""

    UNRELIABLE = "unreliable"
    """Enough laps, but the confidence interval on the rate is too wide to
    mean anything — noisy pace, not a measured trend."""


@dataclass(frozen=True, slots=True)
class DegradationFit:
    """One (driver, stint, compound)'s fitted degradation."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    stint_number: int
    compound: str
    deg_rate_s_per_lap: float
    """Robust (Theil-Sen) slope of fuel-corrected lap time against
    `tyre_age`. Positive means the car gets slower with tyre wear, as
    expected; a fit can still legitimately come back negative or ~0 on a
    very short green stint with no real wear yet."""
    deg_rate_ci_low_s_per_lap: float
    deg_rate_ci_high_s_per_lap: float
    """95% confidence interval on `deg_rate_s_per_lap`, from Theil-Sen's own
    interval — the width is what `reliability` gates on, not a separate
    guess."""
    intercept_s: float
    """Fitted fuel-corrected pace at `tyre_age = 0` — the stint's baseline,
    extrapolated one lap before the first sample."""
    quadratic_term_s_per_lap2: float | None
    """Coefficient of `tyre_age**2` from a SEPARATE robust (Huber) quadratic
    fit, only attempted with at least `degradation_quadratic_min_laps`
    laps — None otherwise. Deliberately not blended into
    `deg_rate_s_per_lap`: the linear rate stays the robust Theil-Sen
    estimate regardless of whether a curvature term was attempted."""
    n_laps_used: int
    r2: float
    """Coefficient of determination of the LINEAR fit against these laps'
    residuals. Theil-Sen does not minimize this quantity, so it can go
    negative on a noisy stint — a diagnostic, not a p-value."""
    reliability: Reliability


def fit_degradation(
    fuel_corrected_frame: pl.DataFrame, params: KernelParams
) -> list[DegradationFit]:
    """One `DegradationFit` per (driver, stint, compound) with at least two
    clean laps carrying both `tyre_age` and `lap_time_fuel_corrected_s`.

    Rows with a null `stint_number`, `tyre_age` or `lap_time_fuel_corrected_s`
    are excluded before fitting — a stint an analytic cannot identify, or a
    lap with no fuel-corrected time, contributes nothing rather than being
    silently coerced into one. A (driver, stint, compound) group with fewer
    than two DISTINCT `tyre_age` values (no variation to regress against, or
    only one usable lap) is skipped entirely rather than returning a
    meaningless fit.

    Pure and deterministic: same frame and params in, identical fits out,
    in the frame's own group order.
    """
    _require_columns(fuel_corrected_frame)
    if fuel_corrected_frame.is_empty():
        return []

    subset = fuel_corrected_frame.filter(
        pl.col("stint_number").is_not_null()
        & pl.col("tyre_age").is_not_null()
        & pl.col("lap_time_fuel_corrected_s").is_not_null()
    )
    if subset.is_empty():
        return []

    fits: list[DegradationFit] = []
    for _keys, group in subset.group_by(list(_GROUP_COLUMNS), maintain_order=True):
        fit = _fit_one_stint(group, params)
        if fit is not None:
            fits.append(fit)
    return fits


def _fit_one_stint(group: pl.DataFrame, params: KernelParams) -> DegradationFit | None:
    ordered = group.sort("tyre_age")
    x = np.asarray(ordered["tyre_age"].to_list(), dtype=float)
    y = np.asarray(ordered["lap_time_fuel_corrected_s"].to_list(), dtype=float)
    if len(set(x.tolist())) < 2:
        return None

    slope, intercept, ci_low, ci_high = _theilslopes(y, x, alpha=0.95)
    if not all(np.isfinite(v) for v in (slope, intercept, ci_low, ci_high)):
        return None

    predicted = intercept + slope * x
    residuals = y - predicted
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    n_laps = x.size
    quadratic_term = None
    if n_laps >= params.degradation_quadratic_min_laps:
        quadratic_term = _fit_quadratic_term(x, y, slope, intercept, params)

    reliability = _reliability(n_laps, ci_high - ci_low, params)

    row0 = group.row(0, named=True)
    return DegradationFit(
        session_year=int(row0["session_year"]),  # type: ignore[arg-type]
        session_round=int(row0["session_round"]),  # type: ignore[arg-type]
        session_type=str(row0["session_type"]),
        driver=str(row0["driver"]),
        stint_number=int(row0["stint_number"]),  # type: ignore[arg-type]
        compound=str(row0["compound"]),
        deg_rate_s_per_lap=slope,
        deg_rate_ci_low_s_per_lap=ci_low,
        deg_rate_ci_high_s_per_lap=ci_high,
        intercept_s=intercept,
        quadratic_term_s_per_lap2=quadratic_term,
        n_laps_used=n_laps,
        r2=r2,
        reliability=reliability,
    )


def _fit_quadratic_term(
    x: np.ndarray, y: np.ndarray, slope0: float, intercept0: float, params: KernelParams
) -> float | None:
    """A separate Huber-loss quadratic fit (`a + b*x + c*x**2`), seeded from
    the linear Theil-Sen result so it converges towards the same trend
    rather than an arbitrary one. Returns `c`, or None if the optimizer did
    not converge — silence beats a curvature number nobody can trust."""

    def residuals(coeffs: np.ndarray) -> np.ndarray:
        a, b, c = coeffs
        return a + b * x + c * x**2 - y

    success, coefficients = _huber_fit(
        residuals, x0=(intercept0, slope0, 0.0), f_scale=params.degradation_huber_scale_s
    )
    return coefficients[2] if success else None


def _huber_fit(
    residuals: Callable[[np.ndarray], np.ndarray],
    x0: tuple[float, float, float],
    f_scale: float,
) -> tuple[bool, tuple[float, float, float]]:
    """`(converged, coefficients)`, precisely typed — see `_theilslopes` for
    why scipy's own loosely-annotated `least_squares` gets a thin wrapper
    rather than being called directly."""
    result = least_squares(residuals, x0=list(x0), loss="huber", f_scale=f_scale)  # type: ignore[arg-type]
    success = bool(result.success)  # type: ignore[arg-type]
    a, b, c = (float(v) for v in result.x)  # type: ignore[arg-type]
    return success, (a, b, c)


def _reliability(n_laps: int, ci_width: float, params: KernelParams) -> Reliability:
    if n_laps < params.min_laps_for_degradation:
        return Reliability.THIN
    if ci_width > params.degradation_max_ci_width_s_per_lap:
        return Reliability.UNRELIABLE
    return Reliability.GOOD


def _require_columns(frame: pl.DataFrame) -> None:
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            f"fuel-corrected lap frame is missing required column(s) {missing}; "
            "build it with f1_kernel.fuel.fuel_correct so the schema is guaranteed"
        )


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

DEGRADATION_FIT_SCHEMA: Schema = {
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "stint_number": pl.Int32(),
    "compound": pl.String(),
    "deg_rate_s_per_lap": pl.Float64(),
    "deg_rate_ci_low_s_per_lap": pl.Float64(),
    "deg_rate_ci_high_s_per_lap": pl.Float64(),
    "intercept_s": pl.Float64(),
    "quadratic_term_s_per_lap2": pl.Float64(),
    "n_laps_used": pl.Int32(),
    "r2": pl.Float64(),
    "reliability": pl.String(),
}


def degradation_fits_to_frame(fits: Sequence[DegradationFit]) -> pl.DataFrame:
    """`fit_degradation`'s output as a frame — one row per fit, for a
    backtest report or further aggregation."""
    if not fits:
        return pl.DataFrame(schema=DEGRADATION_FIT_SCHEMA)
    rows = [
        {
            "session_year": fit.session_year,
            "session_round": fit.session_round,
            "session_type": fit.session_type,
            "driver": fit.driver,
            "stint_number": fit.stint_number,
            "compound": fit.compound,
            "deg_rate_s_per_lap": fit.deg_rate_s_per_lap,
            "deg_rate_ci_low_s_per_lap": fit.deg_rate_ci_low_s_per_lap,
            "deg_rate_ci_high_s_per_lap": fit.deg_rate_ci_high_s_per_lap,
            "intercept_s": fit.intercept_s,
            "quadratic_term_s_per_lap2": fit.quadratic_term_s_per_lap2,
            "n_laps_used": fit.n_laps_used,
            "r2": fit.r2,
            "reliability": fit.reliability.value,
        }
        for fit in fits
    ]
    return pl.DataFrame(rows, schema=DEGRADATION_FIT_SCHEMA)


def summarize_degradation_by_compound(fits: Sequence[DegradationFit]) -> pl.DataFrame:
    """`compound -> mean/median deg rate, spread, reliability breakdown` —
    the headline table for a degradation backtest report. Every fit
    contributes regardless of `reliability`; the reliability counts are
    there so a reader can judge how much of a compound's mean rests on
    `thin`/`unreliable` fits rather than being hidden by an average."""
    frame = degradation_fits_to_frame(fits)
    if frame.is_empty():
        return frame.select(
            pl.col("compound"),
            pl.lit(0).cast(pl.Int64).alias("n_fits"),
        )
    return (
        frame.group_by("compound")
        .agg(
            pl.len().alias("n_fits"),
            pl.col("deg_rate_s_per_lap").mean().alias("mean_deg_rate_s_per_lap"),
            pl.col("deg_rate_s_per_lap").median().alias("median_deg_rate_s_per_lap"),
            pl.col("deg_rate_s_per_lap").std().alias("std_deg_rate_s_per_lap"),
            pl.col("deg_rate_s_per_lap").min().alias("min_deg_rate_s_per_lap"),
            pl.col("deg_rate_s_per_lap").max().alias("max_deg_rate_s_per_lap"),
            (pl.col("reliability") == "good").sum().alias("n_good"),
            (pl.col("reliability") == "thin").sum().alias("n_thin"),
            (pl.col("reliability") == "unreliable").sum().alias("n_unreliable"),
        )
        .sort("compound")
    )


# ---------------------------------------------------------------------------
# compound offset calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompoundOffsetSample:
    """One (driver, session)'s paired delta between a compound and the
    reference compound — the unit of evidence the calibration aggregates.

    Paired rather than pooled RAW intercepts on purpose: two drivers' fresh-
    tyre pace differs by far more than any compound offset does, so
    comparing driver A on mediums against driver B on hards would swamp the
    compound signal in car/driver noise. Pairing within the SAME driver in
    the SAME session cancels that out, leaving (approximately) just the
    compound's own baseline offset — the same logic the backtest's
    same-driver-two-compounds check uses for degradation rates.
    """

    session_year: int
    session_round: int
    session_type: str
    driver: str
    compound: str
    reference_compound: str
    delta_s: float
    """This compound's (lap-count-weighted) mean stint intercept minus the
    driver's own mean intercept on `reference_compound`, same session.
    Positive means this compound is slower than the reference."""
    n_laps: int
    """`min` of the lap counts behind the two intercepts in this pair — the
    weight a calibration run gives this sample."""


@dataclass(frozen=True, slots=True)
class CompoundOffsetCalibrationResult:
    """The fitted per-compound offsets plus everything needed to trust (or
    re-derive) them."""

    compound_offset_s: dict[str, float]
    reference_compound: str
    n_samples: int
    n_sessions: int
    compounds_calibrated: tuple[str, ...]
    method: str
    sessions: tuple[str, ...]
    samples: tuple[CompoundOffsetSample, ...] = field(repr=False)


def calibrate_compound_offsets(
    source: EventSource,
    session_keys: Sequence[SessionKey],
    params: KernelParams,
    *,
    reference_compound: Compound = DEFAULT_REFERENCE_COMPOUND,
    eligible_compounds: frozenset[Compound] = DRY_COMPOUNDS,
) -> CompoundOffsetCalibrationResult:
    """Fit per-compound baseline pace offsets from historical races.

    For every session, `fit_degradation` gives each (driver, stint,
    compound)'s fuel-corrected baseline pace (`intercept_s`, its pace at
    tyre_age 0). For every driver who ran BOTH `reference_compound` and at
    least one other `eligible_compounds` member in the same session — each
    with a fit reliable enough to clear the `thin` floor (see
    `Reliability`) — this pairs the two intercepts (see
    `CompoundOffsetSample`) into one delta. The offset written into
    `params.compound_offset_s` for a compound is the MEDIAN of all its
    deltas across the whole corpus — robust to the one session where a
    driver's compound choice was distorted by damage or a strategy gamble.

    Only `eligible_compounds` (dry compounds by default, see
    `DRY_COMPOUNDS`) are ever compared — pairing a wet-weather stint
    against a dry one would measure the wet-vs-dry gap, not a compound
    effect.

    A known limitation worth restating alongside a fitted number: fuel is
    removed by `fuel_correct`, but track evolution, temperature drift and
    traffic are NOT, and (unlike fuel) they do not cancel between a
    driver's own stints the way they cancel within one stint's laps. A
    compound run later in a race can look faster than its true baseline
    for reasons that have nothing to do with the rubber compound. Sanity
    check a fitted value against `hard slower than medium slower than
    soft` (what every public tyre chart agrees on) before trusting it over
    an existing PROVISIONAL default — see docs/PHASE2.md.

    Raises `ValueError` if not one driver anywhere ran both the reference
    compound and another eligible one reliably enough — silently returning
    `{}` would look like "every compound is identical", which is a claim,
    not an absence of one.
    """
    from f1_kernel.cleaning import clean_laps
    from f1_kernel.fuel import fuel_correct
    from f1_kernel.load import lap_frame

    fuel_corrected_by_session: dict[SessionKey, pl.DataFrame] = {}
    for key in session_keys:
        laps = clean_laps(lap_frame(source, key), params)
        if not laps.is_empty():
            fuel_corrected_by_session[key] = fuel_correct(laps, params)

    return calibrate_compound_offsets_from_frames(
        fuel_corrected_by_session,
        params,
        reference_compound=reference_compound,
        eligible_compounds=eligible_compounds,
    )


def calibrate_compound_offsets_from_frames(
    fuel_corrected_by_session: dict[SessionKey, pl.DataFrame],
    params: KernelParams,
    *,
    reference_compound: Compound = DEFAULT_REFERENCE_COMPOUND,
    eligible_compounds: frozenset[Compound] = DRY_COMPOUNDS,
) -> CompoundOffsetCalibrationResult:
    """The aggregation half of `calibrate_compound_offsets`, split out so it
    can be exercised directly against already fuel-corrected frames."""
    reference_value = reference_compound.value
    eligible_values = {compound.value for compound in eligible_compounds} | {reference_value}
    samples: list[CompoundOffsetSample] = []
    sessions_used: list[str] = []
    compounds_seen: set[str] = set()

    for key, frame in fuel_corrected_by_session.items():
        if frame.is_empty():
            continue
        fits = fit_degradation(frame, params)
        if not fits:
            continue

        by_driver_compound: dict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
        for fit in fits:
            if fit.reliability is Reliability.THIN or fit.compound not in eligible_values:
                continue
            by_driver_compound[(fit.driver, fit.compound)].append(
                (fit.intercept_s, fit.n_laps_used)
            )

        drivers = {driver for driver, _compound in by_driver_compound}
        session_contributed = False
        for driver in drivers:
            reference_entries = by_driver_compound.get((driver, reference_value))
            if not reference_entries:
                continue
            reference_intercept = _weighted_mean(reference_entries)
            reference_laps = sum(n for _v, n in reference_entries)
            for (this_driver, compound), entries in by_driver_compound.items():
                if this_driver != driver or compound == reference_value:
                    continue
                intercept = _weighted_mean(entries)
                n_laps = sum(n for _v, n in entries)
                samples.append(
                    CompoundOffsetSample(
                        session_year=key.year,
                        session_round=key.round,
                        session_type=key.session_type.value,
                        driver=driver,
                        compound=compound,
                        reference_compound=reference_value,
                        delta_s=intercept - reference_intercept,
                        n_laps=min(n_laps, reference_laps),
                    )
                )
                compounds_seen.add(compound)
                session_contributed = True
        if session_contributed:
            sessions_used.append(f"{key.year} R{key.round:02d} {key.session_type.value}")

    if not samples:
        raise ValueError(
            "no driver in the given sessions ran both the reference compound "
            f"({reference_value}) and another eligible compound reliably enough "
            "(both fits must clear the `thin` floor) — nothing to pair"
        )

    offsets: dict[str, float] = {reference_value: 0.0}
    for compound in sorted(compounds_seen):
        deltas = [sample.delta_s for sample in samples if sample.compound == compound]
        offsets[compound] = float(np.median(deltas))

    return CompoundOffsetCalibrationResult(
        compound_offset_s=offsets,
        reference_compound=reference_value,
        n_samples=len(samples),
        n_sessions=len(sessions_used),
        compounds_calibrated=tuple(sorted(compounds_seen)),
        method=(
            "per-driver-per-session paired delta: each driver's lap-count-weighted "
            "mean stint intercept (fuel-corrected pace at tyre_age=0, from "
            "fit_degradation) on a compound minus their own mean intercept on "
            f"{reference_value} in the SAME session — cancels car/driver pace, "
            "isolating the compound's own baseline offset. Reported per compound "
            "as the median paired delta across every driver-session pair found."
        ),
        sessions=tuple(sessions_used),
        samples=tuple(samples),
    )


def _weighted_mean(entries: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _value, weight in entries)
    return sum(value * weight for value, weight in entries) / total_weight
