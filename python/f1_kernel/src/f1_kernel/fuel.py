"""Fuel correction: removing the tank-emptying trend from lap times.

Without this, "degradation" just measures fuel burning off — a stint gets
faster every lap purely because the car is lighter, and a naive slope over
raw lap times conflates that with actual tyre wear. This module has two
jobs, kept deliberately separate:

- **The fuel model** (`estimate_fuel_load_kg`) — how much fuel a car is
  carrying on a given lap. Near-linear: `params.start_fuel_kg` at the green
  flag, burning down towards empty by the chequered flag. The burn RATE is
  derived from the race's own distance when it is known (`start_fuel_kg /
  total_laps`), so a 78-lap Monaco and a 53-lap Monza both target empty at
  their own finish line rather than sharing one constant; `fuel_burn_kg_per_lap`
  is the fallback when a race's total lap count is not available at all.
  Both are documented APPROXIMATIONS (see their `KernelParams` field docs) —
  real consumption varies lap to lap with fuel-saving, safety cars and
  engine modes, and teams do not literally plan to arrive on empty.
- **The correction** (`fuel_correct`) — given that fuel model and a
  calibrated `fuel_coef_s_per_kg` (seconds of lap time per kilogram
  carried), re-expresses every lap's time as if the car had carried a fixed
  reference load (`params.fuel_reference_kg`, 0 kg by default — "as if the
  tank were empty"). The correction is a simple affine shift, hence exactly
  reversible: `raw = corrected + fuel_coef * (fuel_load - reference)`.

`calibrate_fuel_coefficient` is the one-time (but re-runnable) offline
routine that fits `fuel_coef_s_per_kg` itself from historical data — see
`python/f1_kernel/tools/calibrate_fuel.py`, whose OUTPUT is what is
committed into `KernelParams`' default and `python/f1_kernel/calibration/
fuel_coefficient.json`. This module does not calibrate degradation; that is
`f1_kernel.degradation`, built on top of `fuel_correct`'s output.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from f1_domain import Compound, SessionKey
from f1_kernel._robust_stats import theilslopes as _theilslopes
from f1_kernel.cleaning import clean_laps
from f1_kernel.params import KernelParams
from f1_kernel.schemas import LAP_FRAME_SCHEMA, Schema, empty_frame
from f1_kernel.sources import EventSource

FUEL_COLUMNS: tuple[str, ...] = ("fuel_load_kg", "lap_time_fuel_corrected_s")

FUEL_CORRECTED_LAP_FRAME_SCHEMA: Schema = {
    **LAP_FRAME_SCHEMA,
    "fuel_load_kg": pl.Float64(),
    "lap_time_fuel_corrected_s": pl.Float64(),
}
"""The lap frame plus the fuel model's output — what `fuel_correct` returns."""

_REQUIRED_COLUMNS = (
    "session_year",
    "session_round",
    "session_type",
    "lap_number",
    "lap_time_s",
)

_SESSION_GROUP_COLUMNS = ("session_year", "session_round", "session_type")

DEFAULT_LOW_DEGRADATION_COMPOUNDS: tuple[Compound, ...] = (Compound.HARD, Compound.MEDIUM)
"""Compounds preferred for calibration: the ones where tyre wear contributes
the least to a stint's lap-time trend, so the leftover slope against fuel
load is (mostly) the fuel effect the prompt asks us to isolate."""


# ---------------------------------------------------------------------------
# the fuel model
# ---------------------------------------------------------------------------


def estimate_fuel_load_kg(lap_number: int, total_laps: int | None, params: KernelParams) -> float:
    """Estimated fuel load carried at the START of `lap_number` (1-based),
    in kilograms — a near-linear burn from `params.start_fuel_kg` down to
    ~empty by the race's last lap.

    The burn rate is `start_fuel_kg / total_laps` when the race distance is
    known, so the model spans the ACTUAL race rather than assuming every
    race burns fuel at the same rate; it falls back to
    `params.fuel_burn_kg_per_lap` when `total_laps` is None or non-positive
    (no session context at all — e.g. a bare list of laps with no known race
    length). Clamped at zero: a lap far beyond the modelled distance (a red
    flag restart, an extra formation lap) does not go fuel-negative.
    """
    if total_laps is not None and total_laps > 0:
        burn_rate = params.start_fuel_kg / total_laps
    else:
        burn_rate = params.fuel_burn_kg_per_lap
    remaining = params.start_fuel_kg - burn_rate * (lap_number - 1)
    return max(0.0, remaining)


def fuel_correction_delta_s(fuel_load_kg: float, params: KernelParams) -> float:
    """The seconds `fuel_correct` subtracts from a lap carrying `fuel_load_kg`
    to bring it to `params.fuel_reference_kg`. Exposed so the correction and
    its inverse (`raw = corrected + delta`) share one formula."""
    return params.fuel_coef_s_per_kg * (fuel_load_kg - params.fuel_reference_kg)


def _fuel_load_expr(params: KernelParams) -> pl.Expr:
    """`estimate_fuel_load_kg`, vectorized over a frame carrying a
    `_total_laps` column (the race length inferred per session group)."""
    total_laps = pl.col("_total_laps")
    burn_rate = (
        pl.when(total_laps > 0)
        .then(pl.lit(params.start_fuel_kg) / total_laps)
        .otherwise(pl.lit(params.fuel_burn_kg_per_lap))
    )
    remaining = pl.lit(params.start_fuel_kg) - burn_rate * (pl.col("lap_number") - 1)
    return pl.when(remaining > 0).then(remaining).otherwise(0.0)


# ---------------------------------------------------------------------------
# the correction
# ---------------------------------------------------------------------------


def fuel_correct(
    clean_lap_frame: pl.DataFrame, params: KernelParams, *, total_laps: int | None = None
) -> pl.DataFrame:
    """Add `fuel_load_kg` and `lap_time_fuel_corrected_s` to a lap frame.

    Every lap is corrected to the common reference load
    `params.fuel_reference_kg` using `params.fuel_coef_s_per_kg`:

        lap_time_fuel_corrected_s = (
            lap_time_s - fuel_coef_s_per_kg * (fuel_load_kg - fuel_reference_kg)
        )

    A stint run early in a heavy race gets time added back (it looks faster
    than it "should" once its extra fuel is accounted for); a late, light
    stint gets time subtracted. `lap_time_s` NULL laps stay NULL — nothing
    is invented for a lap the feed dropped.

    The race's own length (for the fuel model's burn rate) is inferred as
    the highest `lap_number` observed in `clean_lap_frame`, grouped by
    `(session_year, session_round, session_type)` — an approximation when
    the input has already had its final laps filtered out, but a reasonable
    one: some driver in the field almost always has a clean lap near the end.
    Pass `total_laps` explicitly when the frame cannot know it: a frame
    sliced at a mid-race decision lap (the undercut walk-forward, a live
    session in progress) would otherwise infer a race as long as the slice
    and over-remove the fuel trend, biasing every degradation rate fitted on
    it. The override applies to every session group in the frame, so pass it
    only for single-session frames.

    Pure and deterministic: same frame and params in, identical frame out.
    Row order is preserved.
    """
    _require_columns(clean_lap_frame)
    if clean_lap_frame.is_empty():
        return empty_frame(FUEL_CORRECTED_LAP_FRAME_SCHEMA)

    if total_laps is not None:
        with_totals = clean_lap_frame.with_columns(
            pl.lit(total_laps, dtype=pl.Int64).alias("_total_laps")
        )
    else:
        with_totals = clean_lap_frame.with_columns(
            pl.col("lap_number").max().over(list(_SESSION_GROUP_COLUMNS)).alias("_total_laps")
        )
    fuel_load = _fuel_load_expr(params)
    corrected = pl.col("lap_time_s") - params.fuel_coef_s_per_kg * (
        fuel_load - params.fuel_reference_kg
    )

    result = with_totals.with_columns(
        fuel_load.alias("fuel_load_kg"),
        corrected.alias("lap_time_fuel_corrected_s"),
    )
    return result.select([*clean_lap_frame.columns, *FUEL_COLUMNS])


def _require_columns(laps: pl.DataFrame) -> None:
    missing = [column for column in _REQUIRED_COLUMNS if column not in laps.columns]
    if missing:
        raise ValueError(
            f"lap frame is missing required column(s) {missing}; "
            "build it with f1_kernel.lap_frame so the schema is guaranteed"
        )


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FuelStintEstimate:
    """One (driver, stint)'s contribution to the calibration.

    Deliberately NOT a regression of lap time against estimated fuel load
    directly: within a single stint, estimated fuel load is a NOISELESS
    linear function of lap number (our own model, not a measurement), and
    so is tyre age (lap number minus the stint's start lap). The two are
    therefore perfectly collinear within one stint — regressing on fuel
    load there cannot separate a fuel effect from degradation at all, it is
    just `-1/burn_rate` times whatever the raw lap_time-vs-lap_number slope
    already was, REGARDLESS of the true fuel coefficient (confirmed
    empirically before this was written: it produces nonsense, including
    the wrong sign, over the real 2024 corpus).

    What breaks the collinearity is that `burn_rate_kg_per_lap` — the
    fuel model's derived burn rate, `start_fuel_kg / total_laps` — VARIES
    across circuits (a 78-lap Monaco burns fuel far more slowly per lap
    than a 44-lap Spa), while a stint's tyre-age slope does not depend on
    lap distance at all. So each stint contributes its raw (uncorrected)
    lap-time-vs-lap-number slope AND that race's burn rate; the
    module-level regression is across THESE pairs, not within one stint.
    """

    session_year: int
    session_round: int
    session_type: str
    driver: str
    stint_number: int
    compound: str
    n_laps: int
    burn_rate_kg_per_lap: float
    raw_slope_s_per_lap: float
    """Robust (Theil-Sen) slope of clean green-flag `lap_time_s` against
    RACE lap number over the stint — includes both fuel and degradation."""


@dataclass(frozen=True, slots=True)
class FuelCalibrationResult:
    """The fitted coefficient plus everything needed to trust (or
    re-derive) it — CLAUDE.md's "value AND a confidence signal" applied to
    an offline calibration rather than a per-race analytic."""

    fuel_coef_s_per_kg: float
    ci_low_s_per_kg: float
    ci_high_s_per_kg: float
    n_stints: int
    n_sessions: int
    compounds_used: tuple[str, ...]
    method: str
    sessions: tuple[str, ...]
    per_stint: tuple[FuelStintEstimate, ...] = field(repr=False)


def calibrate_fuel_coefficient(
    source: EventSource,
    session_keys: Sequence[SessionKey],
    params: KernelParams,
    *,
    preferred_compounds: Sequence[Compound] = DEFAULT_LOW_DEGRADATION_COMPOUNDS,
    min_stint_laps: int = 5,
) -> FuelCalibrationResult:
    """Fit `fuel_coef_s_per_kg` from historical races.

    Method, in two stages (see `FuelStintEstimate` for why a single-stint
    regression against fuel load cannot work at all):

    1. For every (driver, stint) run on a low-degradation compound
       (`preferred_compounds`, hard/medium by default — a preference for
       where tyre wear contributes least to the trend, not a hard
       requirement; a session with nothing suitable contributes zero
       stints, not a crash), fit a robust (Theil-Sen) slope of clean
       green-flag `lap_time_s` against RACE lap number, and pair it with
       that race's fuel burn rate (`start_fuel_kg / total_laps` — DIFFERENT
       circuits run very different race distances, e.g. ~44 laps at Spa vs
       ~78 at Monaco, so this varies a lot across a real season).
    2. Regress those per-stint slopes against their burn rates, again with
       Theil-Sen: `raw_slope = avg_degradation - fuel_coef * burn_rate`.
       The (negated) slope of THIS regression is `fuel_coef_s_per_kg` — it
       is identified by cross-circuit variation in burn rate, not by
       assuming any one stint's degradation is exactly zero. Theil-Sen's
       own confidence interval on that slope is the calibration's
       uncertainty, so it is deterministic: re-running against the same
       corpus reproduces the identical number.

    Raises `ValueError` if not one stint anywhere qualifies, or if every
    qualifying stint came from races of the identical length (no burn-rate
    variation to regress against) — a calibration that silently produced
    nothing, or a number with no real information behind it, would be worse
    than one that says so.
    """
    from f1_kernel.load import lap_frame  # local: avoids a load->fuel import cycle

    clean_by_session: dict[SessionKey, pl.DataFrame] = {}
    for key in session_keys:
        laps = clean_laps(lap_frame(source, key), params)
        if not laps.is_empty():
            clean_by_session[key] = laps

    return calibrate_fuel_coefficient_from_frames(
        clean_by_session,
        params,
        preferred_compounds=preferred_compounds,
        min_stint_laps=min_stint_laps,
    )


def calibrate_fuel_coefficient_from_frames(
    clean_laps_by_session: dict[SessionKey, pl.DataFrame],
    params: KernelParams,
    *,
    preferred_compounds: Sequence[Compound] = DEFAULT_LOW_DEGRADATION_COMPOUNDS,
    min_stint_laps: int = 5,
) -> FuelCalibrationResult:
    """The aggregation half of `calibrate_fuel_coefficient`, split out so it
    can be exercised directly against already-built clean lap frames — a
    unit test does not need a real `EventSource` to check the arithmetic."""
    estimates: list[FuelStintEstimate] = []
    sessions_used: list[str] = []
    compounds_seen: set[str] = set()

    for key, laps in clean_laps_by_session.items():
        if laps.is_empty():
            continue
        total_laps = laps["lap_number"].max()
        total_laps = int(total_laps) if isinstance(total_laps, int | float) else None
        if total_laps is None or total_laps <= 0:
            continue
        burn_rate = params.start_fuel_kg / total_laps

        session_stints = _stint_estimates_for_session(
            laps, key, burn_rate, preferred_compounds, min_stint_laps
        )
        if session_stints:
            estimates.extend(session_stints)
            compounds_seen.update(estimate.compound for estimate in session_stints)
            sessions_used.append(f"{key.year} R{key.round:02d} {key.session_type.value}")

    if not estimates:
        raise ValueError(
            "no stint anywhere in the given sessions qualified for fuel calibration "
            f"(compounds tried: {[c.value for c in preferred_compounds]}, "
            f"min_stint_laps={min_stint_laps})"
        )

    burn_rates = np.array([estimate.burn_rate_kg_per_lap for estimate in estimates], dtype=float)
    raw_slopes = np.array([estimate.raw_slope_s_per_lap for estimate in estimates], dtype=float)
    if len(set(burn_rates.tolist())) < 2:
        raise ValueError(
            "every qualifying stint came from races of the same length; fuel_coef_s_per_kg "
            "is identified by cross-circuit variation in burn rate, so there is nothing to "
            "regress against — widen `session_keys` to more than one circuit"
        )

    # raw_slope = intercept - fuel_coef * burn_rate, so fuel_coef is the
    # NEGATED slope of raw_slope regressed on burn_rate.
    meta_slope, _meta_intercept, ci_low_slope, ci_high_slope = _theilslopes(
        raw_slopes, burn_rates, alpha=0.95
    )
    fuel_coef = -meta_slope
    if not math.isfinite(fuel_coef):
        raise ValueError("the burn-rate regression did not produce a finite slope")

    return FuelCalibrationResult(
        fuel_coef_s_per_kg=fuel_coef,
        ci_low_s_per_kg=-ci_high_slope,
        ci_high_s_per_kg=-ci_low_slope,
        n_stints=len(estimates),
        n_sessions=len(sessions_used),
        compounds_used=tuple(sorted(compounds_seen)),
        method=(
            "cross-circuit regression: per-(driver,stint) Theil-Sen slope of clean "
            "green-flag lap_time_s vs race lap number, on low-degradation compounds "
            f"({'/'.join(sorted(compounds_seen))}), regressed (Theil-Sen, negated) "
            "against that race's fuel burn rate (start_fuel_kg / total_laps); 95% CI "
            "is Theil-Sen's own confidence interval on that regression's slope."
        ),
        sessions=tuple(sessions_used),
        per_stint=tuple(estimates),
    )


def _stint_estimates_for_session(
    laps: pl.DataFrame,
    key: SessionKey,
    burn_rate: float,
    preferred_compounds: Sequence[Compound],
    min_stint_laps: int,
) -> list[FuelStintEstimate]:
    wanted = {compound.value for compound in preferred_compounds}
    subset = laps.filter(
        pl.col("compound").is_in(list(wanted))
        & pl.col("stint_number").is_not_null()
        & pl.col("lap_time_s").is_not_null()
    )
    if subset.is_empty():
        return []

    results: list[FuelStintEstimate] = []
    for (driver, stint_number, compound), group in subset.group_by(
        ["driver", "stint_number", "compound"]
    ):
        if group.height < min_stint_laps:
            continue
        lap_numbers = group["lap_number"].to_list()
        lap_times = group["lap_time_s"].to_list()
        if len(set(lap_numbers)) < 2:
            continue  # no variation to regress against
        slope, _intercept, _low, _high = _theilslopes(
            np.asarray(lap_times, dtype=float), np.asarray(lap_numbers, dtype=float), alpha=0.95
        )
        if not math.isfinite(slope):
            continue
        results.append(
            FuelStintEstimate(
                session_year=key.year,
                session_round=key.round,
                session_type=key.session_type.value,
                driver=str(driver),
                stint_number=int(stint_number),  # type: ignore[arg-type]
                compound=str(compound),
                n_laps=group.height,
                burn_rate_kg_per_lap=burn_rate,
                raw_slope_s_per_lap=slope,
            )
        )
    return results


__all__ = [
    "DEFAULT_LOW_DEGRADATION_COMPOUNDS",
    "FUEL_COLUMNS",
    "FUEL_CORRECTED_LAP_FRAME_SCHEMA",
    "FuelCalibrationResult",
    "FuelStintEstimate",
    "calibrate_fuel_coefficient",
    "calibrate_fuel_coefficient_from_frames",
    "estimate_fuel_load_kg",
    "fuel_correct",
    "fuel_correction_delta_s",
]
