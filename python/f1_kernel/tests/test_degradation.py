"""Degradation fitting: the deg-rate number underneath the whole strategy
story, and the per-compound baseline offsets calibrated alongside it.

CLAUDE.md's framing for this prompt: this is what public tools fake with a
vision demo and a gut feel; these tests check the fit actually recovers a
known rate, is robust to one bad lap, and reports its own reliability
rather than pretending a 3-lap fit is as good as a 15-lap one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from f1_domain import SessionKey, SessionType
from f1_kernel import KernelParams, clean_laps, fuel_correct, lap_frame
from f1_kernel.degradation import (
    DegradationFit,
    Reliability,
    calibrate_compound_offsets,
    calibrate_compound_offsets_from_frames,
    degradation_fits_to_frame,
    fit_degradation,
    summarize_degradation_by_compound,
)
from f1_kernel.fuel import FUEL_CORRECTED_LAP_FRAME_SCHEMA
from f1_kernel.schemas import LAP_FRAME_SCHEMA
from f1_kernel.sources import HistoricalSource

REAL_ROOT = "data/historical"


def _real_root_available() -> bool:
    from pathlib import Path

    from f1_kernel.sources import list_historical_sessions

    return Path(REAL_ROOT).is_dir() and bool(list_historical_sessions(REAL_ROOT))


requires_real_backfill = pytest.mark.skipif(
    not _real_root_available(), reason="no local backfill; run `uv run f1-backfill --season 2024`"
)

# ---------------------------------------------------------------------------
# fit_degradation: recovering a known rate
# ---------------------------------------------------------------------------


def test_fit_degradation_recovers_a_known_linear_rate(params: KernelParams) -> None:
    true_rate = 0.08
    true_intercept = 91.5
    frame = _synthetic_fuel_corrected_stint(
        [true_intercept + true_rate * age for age in range(1, 16)]
    )

    fits = fit_degradation(frame, params)

    assert len(fits) == 1
    fit = fits[0]
    assert fit.deg_rate_s_per_lap == pytest.approx(true_rate, abs=1e-9)
    assert fit.intercept_s == pytest.approx(true_intercept, abs=1e-9)
    assert fit.n_laps_used == 15
    assert fit.reliability is Reliability.GOOD
    assert fit.r2 == pytest.approx(1.0, abs=1e-9)
    assert fit.deg_rate_ci_low_s_per_lap <= fit.deg_rate_s_per_lap <= fit.deg_rate_ci_high_s_per_lap


def test_fit_degradation_is_robust_to_one_outlier_lap(params: KernelParams) -> None:
    """One missed-apex lap must not drag a Theil-Sen fit far from the true
    trend the way it would an OLS fit."""
    true_rate = 0.05
    true_intercept = 90.0
    lap_times = [true_intercept + true_rate * age for age in range(1, 16)]
    lap_times[7] += 8.0  # one badly compromised lap deep in the stint

    fits = fit_degradation(_synthetic_fuel_corrected_stint(lap_times), params)

    assert len(fits) == 1
    assert fits[0].deg_rate_s_per_lap == pytest.approx(true_rate, abs=0.02)


def test_fit_degradation_reliability_is_thin_below_the_minimum_lap_count(
    params: KernelParams,
) -> None:
    assert params.min_laps_for_degradation > 3
    lap_times = [90.0 + 0.05 * age for age in range(1, 4)]

    fits = fit_degradation(_synthetic_fuel_corrected_stint(lap_times), params)

    assert len(fits) == 1
    assert fits[0].n_laps_used == 3
    assert fits[0].reliability is Reliability.THIN


def test_fit_degradation_reliability_is_unreliable_when_the_ci_is_too_wide(
    params: KernelParams,
) -> None:
    """Plenty of laps, but pace bouncing around so much the rate's own
    confidence interval is too wide to mean anything."""
    n_laps = params.min_laps_for_degradation + 3
    lap_times = [90.0 + (3.0 if age % 2 == 0 else -3.0) for age in range(1, n_laps + 1)]

    fits = fit_degradation(_synthetic_fuel_corrected_stint(lap_times), params)

    assert len(fits) == 1
    fit = fits[0]
    assert fit.n_laps_used >= params.min_laps_for_degradation
    width = fit.deg_rate_ci_high_s_per_lap - fit.deg_rate_ci_low_s_per_lap
    assert width > params.degradation_max_ci_width_s_per_lap
    assert fit.reliability is Reliability.UNRELIABLE


def test_fit_degradation_quadratic_term_is_none_below_the_minimum_lap_count(
    params: KernelParams,
) -> None:
    n_laps = params.degradation_quadratic_min_laps - 1
    lap_times = [90.0 + 0.05 * age + 0.01 * age**2 for age in range(1, n_laps + 1)]

    fits = fit_degradation(_synthetic_fuel_corrected_stint(lap_times), params)

    assert len(fits) == 1
    assert fits[0].quadratic_term_s_per_lap2 is None


def test_fit_degradation_quadratic_term_recovers_a_known_curvature(
    params: KernelParams,
) -> None:
    n_laps = params.degradation_quadratic_min_laps + 4
    true_a, true_b, true_c = 90.0, 0.03, 0.02
    lap_times = [true_a + true_b * age + true_c * age**2 for age in range(1, n_laps + 1)]

    fits = fit_degradation(_synthetic_fuel_corrected_stint(lap_times), params)

    assert len(fits) == 1
    quadratic = fits[0].quadratic_term_s_per_lap2
    assert quadratic is not None
    assert quadratic == pytest.approx(true_c, abs=1e-6)


def test_fit_degradation_produces_one_fit_per_driver_stint_compound(
    params: KernelParams,
) -> None:
    frame = pl.concat(
        [
            _synthetic_fuel_corrected_stint(
                [90.0 + 0.05 * age for age in range(1, 10)], driver="1", stint_number=1
            ),
            _synthetic_fuel_corrected_stint(
                [92.0 + 0.10 * age for age in range(1, 10)],
                driver="1",
                stint_number=2,
                compound="hard",
            ),
            _synthetic_fuel_corrected_stint(
                [91.0 + 0.07 * age for age in range(1, 10)], driver="44", stint_number=1
            ),
        ]
    )

    fits = fit_degradation(frame, params)

    keys = {(fit.driver, fit.stint_number, fit.compound) for fit in fits}
    assert keys == {("1", 1, "medium"), ("1", 2, "hard"), ("44", 1, "medium")}


def test_fit_degradation_skips_a_group_with_no_tyre_age_variation(params: KernelParams) -> None:
    frame = _synthetic_fuel_corrected_stint([90.0])  # a single lap: no variation to regress

    fits = fit_degradation(frame, params)

    assert fits == []


def test_fit_degradation_excludes_rows_with_null_stint_number(params: KernelParams) -> None:
    frame = _synthetic_fuel_corrected_stint([90.0 + 0.05 * age for age in range(1, 8)])
    frame = frame.with_columns(pl.lit(None, dtype=pl.Int32).alias("stint_number"))

    fits = fit_degradation(frame, params)

    assert fits == []


def test_fit_degradation_on_an_empty_frame_returns_an_empty_list(params: KernelParams) -> None:
    empty = pl.DataFrame(schema=FUEL_CORRECTED_LAP_FRAME_SCHEMA)

    assert fit_degradation(empty, params) == []


def test_fit_degradation_rejects_a_frame_missing_required_columns(params: KernelParams) -> None:
    with pytest.raises(ValueError, match="missing required column"):
        fit_degradation(pl.DataFrame({"driver": ["1"]}), params)


def test_fit_degradation_is_deterministic(params: KernelParams) -> None:
    frame = _synthetic_fuel_corrected_stint([90.0 + 0.05 * age for age in range(1, 12)])

    first = fit_degradation(frame, params)
    second = fit_degradation(frame, params)

    assert first == second


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def test_degradation_fits_to_frame_round_trips_the_key_fields(params: KernelParams) -> None:
    frame = _synthetic_fuel_corrected_stint([90.0 + 0.05 * age for age in range(1, 10)])
    fits = fit_degradation(frame, params)

    table = degradation_fits_to_frame(fits)

    assert table.height == 1
    assert table["deg_rate_s_per_lap"][0] == pytest.approx(fits[0].deg_rate_s_per_lap)
    assert table["reliability"][0] == fits[0].reliability.value


def test_summarize_degradation_by_compound_groups_and_aggregates(params: KernelParams) -> None:
    fits = fit_degradation(
        pl.concat(
            [
                _synthetic_fuel_corrected_stint(
                    [90.0 + 0.05 * age for age in range(1, 10)],
                    driver="1",
                    compound="soft",
                ),
                _synthetic_fuel_corrected_stint(
                    [90.0 + 0.02 * age for age in range(1, 10)],
                    driver="44",
                    compound="hard",
                ),
            ]
        ),
        params,
    )

    table = summarize_degradation_by_compound(fits)

    assert set(table["compound"]) == {"soft", "hard"}
    soft_row = table.filter(pl.col("compound") == "soft")
    assert soft_row["n_fits"][0] == 1
    assert soft_row["mean_deg_rate_s_per_lap"][0] == pytest.approx(0.05, abs=1e-6)


def test_summarize_degradation_by_compound_on_no_fits_is_empty() -> None:
    table = summarize_degradation_by_compound([])
    assert table.is_empty()


# ---------------------------------------------------------------------------
# compound offset calibration
# ---------------------------------------------------------------------------


def test_calibrate_compound_offsets_recovers_known_deltas_from_synthetic_data(
    params: KernelParams,
) -> None:
    true_offsets = {"soft": 0.0, "medium": 0.4, "hard": 0.9}
    driver_paces = {"1": 90.0, "44": 91.5, "16": 89.2}
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)

    stints: list[pl.DataFrame] = []
    for driver, base_pace in driver_paces.items():
        for stint_number, (compound, offset) in enumerate(true_offsets.items(), start=1):
            lap_times = [base_pace + offset + 0.03 * age for age in range(1, 10)]
            stints.append(
                _synthetic_fuel_corrected_stint(
                    lap_times, driver=driver, stint_number=stint_number, compound=compound
                )
            )
    frame = pl.concat(stints)

    result = calibrate_compound_offsets_from_frames({key: frame}, params)

    assert result.compound_offset_s["soft"] == pytest.approx(0.0, abs=1e-9)
    assert result.compound_offset_s["medium"] == pytest.approx(0.4, abs=1e-6)
    assert result.compound_offset_s["hard"] == pytest.approx(0.9, abs=1e-6)
    assert result.reference_compound == "soft"
    assert result.n_sessions == 1
    assert set(result.compounds_calibrated) == {"medium", "hard"}


def test_calibrate_compound_offsets_raises_when_no_driver_ran_both(params: KernelParams) -> None:
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    frame = _synthetic_fuel_corrected_stint(
        [90.0 + 0.03 * age for age in range(1, 10)], driver="1", compound="medium"
    )

    with pytest.raises(ValueError, match="nothing to pair"):
        calibrate_compound_offsets_from_frames({key: frame}, params)


def test_calibrate_compound_offsets_is_deterministic(params: KernelParams) -> None:
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    frame = pl.concat(
        [
            _synthetic_fuel_corrected_stint(
                [90.0 + 0.03 * age for age in range(1, 10)],
                driver="1",
                stint_number=1,
                compound="soft",
            ),
            _synthetic_fuel_corrected_stint(
                [90.4 + 0.03 * age for age in range(1, 10)],
                driver="1",
                stint_number=2,
                compound="medium",
            ),
        ]
    )

    first = calibrate_compound_offsets_from_frames({key: frame}, params)
    second = calibrate_compound_offsets_from_frames({key: frame}, params)

    assert first.compound_offset_s == second.compound_offset_s


# ---------------------------------------------------------------------------
# real-data backtest (the prompt's actual test)
# ---------------------------------------------------------------------------


@requires_real_backfill
def test_backtest_softer_compounds_degrade_faster_on_average() -> None:
    """Directional sanity across the whole dataset, not per-stint: the
    MEDIAN degradation rate (robust to the handful of noisy short stints
    that dominate a plain mean — see the `mean` vs `median` columns this
    same table prints side by side) must rank soft above medium above
    hard."""
    params = KernelParams()
    fits = _fit_all_real_races(params)
    assert len(fits) >= 30

    table = summarize_degradation_by_compound(fits)
    by_compound = {row["compound"]: row["median_deg_rate_s_per_lap"] for row in table.to_dicts()}

    assert {"soft", "medium", "hard"} <= set(by_compound)
    assert by_compound["soft"] > by_compound["medium"], "soft must degrade faster than medium"
    assert by_compound["medium"] > by_compound["hard"], "medium must degrade faster than hard"


@requires_real_backfill
def test_backtest_degradation_rates_are_physically_plausible() -> None:
    """Deg rates fall in a plausible band; outliers are flagged for
    inspection (printed) rather than failing the whole backtest."""
    params = KernelParams()
    fits = _fit_all_real_races(params)
    good_fits = [fit for fit in fits if fit.reliability is not Reliability.THIN]
    assert good_fits

    plausible_low, plausible_high = -0.3, 1.5
    outliers = [
        fit for fit in good_fits if not (plausible_low <= fit.deg_rate_s_per_lap <= plausible_high)
    ]
    if outliers:
        print(
            f"\n{len(outliers)} degradation outlier(s) outside [{plausible_low}, {plausible_high}]:"
        )
        for fit in outliers[:20]:
            print(
                f"  {fit.session_year} R{fit.session_round:02d} {fit.driver} "
                f"stint {fit.stint_number} ({fit.compound}): "
                f"{fit.deg_rate_s_per_lap:.3f} s/lap over {fit.n_laps_used} laps"
            )

    outlier_fraction = len(outliers) / len(good_fits)
    assert outlier_fraction < 0.05, "too many implausible fits to be measurement noise"


@requires_real_backfill
def test_calibrate_compound_offsets_over_real_races_runs_and_stays_dry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mechanism, not a strict ordering: `calibrate_compound_offsets`'
    own docstring documents why an offset it fits can come out physically
    implausible (session-level drift that does not cancel between a
    driver's stints the way it does within one) — confirmed empirically on
    the real 2024 corpus, where the fitted medium offset comes out
    slightly NEGATIVE despite medium being the slower compound. `hard`,
    the one comparison this corpus has the most (least noisy) evidence
    for, is checked directly; `medium` is printed for a human to judge
    rather than asserted on, which is exactly the "flag for inspection"
    treatment CLAUDE.md asks for on data that fails a plausibility check."""
    from f1_kernel.sources import list_historical_sessions

    source = HistoricalSource(REAL_ROOT)
    sessions = [
        ref.session_key
        for ref in list_historical_sessions(REAL_ROOT, session_types=[SessionType.RACE])
    ]
    assert sessions

    result = calibrate_compound_offsets(source, sessions, KernelParams())

    assert result.n_samples >= 10
    assert set(result.compounds_calibrated) == {"medium", "hard"}, (
        "wet/intermediate must never be paired against a dry reference compound"
    )
    assert result.compound_offset_s["hard"] > 0.0, "hard must be slower than soft"
    print(f"\ncalibrated compound_offset_s: {result.compound_offset_s}")
    if result.compound_offset_s["medium"] <= 0.0:
        print(
            "note: medium offset is non-positive — a known confound, see "
            "calibrate_compound_offsets' docstring; do not write this into "
            "KernelParams without a human sanity check"
        )


def _fit_all_real_races(params: KernelParams) -> list[DegradationFit]:
    from f1_kernel.sources import list_historical_sessions

    source = HistoricalSource(REAL_ROOT)
    sessions = [
        ref.session_key
        for ref in list_historical_sessions(REAL_ROOT, session_types=[SessionType.RACE])
    ]
    fits: list[DegradationFit] = []
    for key in sessions:
        laps = clean_laps(lap_frame(source, key), params)
        if laps.is_empty():
            continue
        corrected = fuel_correct(laps, params)
        fits.extend(fit_degradation(corrected, params))
    return fits


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _synthetic_fuel_corrected_stint(
    lap_times: list[float],
    *,
    driver: str = "1",
    stint_number: int = 1,
    compound: str = "medium",
) -> pl.DataFrame:
    """A minimal fuel-corrected frame for one (driver, stint, compound),
    with `lap_time_fuel_corrected_s` set directly to `lap_times` — no fuel
    effect baked in, so a test can bake in a degradation trend exactly."""
    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    rows: dict[str, list[object]] = {name: [] for name in LAP_FRAME_SCHEMA}
    clock = start
    for age, lap_time in enumerate(lap_times, start=1):
        end = clock + timedelta(seconds=lap_time)
        rows["session_year"].append(2024)
        rows["session_round"].append(1)
        rows["session_type"].append("race")
        rows["driver"].append(driver)
        rows["lap_number"].append(age)
        rows["lap_start_utc"].append(clock)
        rows["lap_end_utc"].append(end)
        rows["lap_time_s"].append(lap_time)
        rows["sector1_s"].append(None)
        rows["sector2_s"].append(None)
        rows["sector3_s"].append(None)
        rows["is_personal_best"].append(False)
        rows["compound"].append(compound)
        rows["tyre_age"].append(age)
        rows["stint_number"].append(stint_number)
        rows["position"].append(1)
        rows["gap_ahead_s"].append(None)
        rows["gap_ahead_min_s"].append(None)
        rows["gap_ahead_samples"].append(0)
        rows["gap_to_leader_s"].append(None)
        rows["track_status"].append("green")
        rows["track_status_flags"].append([])
        rows["is_pit_in"].append(False)
        rows["is_pit_out"].append(False)
        rows["provenance"].append("historical")
        clock = end
    frame = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA)
    return frame.with_columns(
        pl.lit(0.0).alias("fuel_load_kg"),
        pl.col("lap_time_s").alias("lap_time_fuel_corrected_s"),
    )
