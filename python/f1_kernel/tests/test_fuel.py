"""Fuel correction: the model, the correction, and its calibration.

CLAUDE.md's warning for this prompt: without fuel correction, "degradation"
just measures the tank emptying. These tests check the correction actually
removes that trend, that it is a well-behaved (monotonic, reversible)
transform, and that the calibration routine that fits its coefficient
produces a number in a physically plausible range from real races.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from kernel_fixture import SC_RACE

from f1_domain import SessionKey, SessionType
from f1_kernel import KernelParams, clean_laps, lap_frame
from f1_kernel.fuel import (
    calibrate_fuel_coefficient,
    calibrate_fuel_coefficient_from_frames,
    estimate_fuel_load_kg,
    fuel_correct,
    fuel_correction_delta_s,
)
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
# the fuel model
# ---------------------------------------------------------------------------


def test_fuel_load_decreases_across_the_race(params: KernelParams) -> None:
    first = estimate_fuel_load_kg(1, 57, params)
    middle = estimate_fuel_load_kg(29, 57, params)
    last = estimate_fuel_load_kg(57, 57, params)

    assert first == params.start_fuel_kg
    assert first > middle > last
    assert last >= 0.0


def test_fuel_load_burn_rate_is_derived_from_race_distance(params: KernelParams) -> None:
    """The burn rate is `start_fuel_kg / total_laps`, so different race
    lengths burn at different per-lap rates rather than sharing one
    constant — a 78-lap Monaco sips fuel far more slowly per lap than a
    53-lap Monza."""
    monza_burn_rate = params.start_fuel_kg / 53
    monaco_burn_rate = params.start_fuel_kg / 78
    assert monza_burn_rate > monaco_burn_rate

    # One lap from the flag, almost exactly one lap's worth of fuel remains.
    monza_last = estimate_fuel_load_kg(53, 53, params)
    monaco_last = estimate_fuel_load_kg(78, 78, params)
    assert monza_last == pytest.approx(monza_burn_rate)
    assert monaco_last == pytest.approx(monaco_burn_rate)
    assert monza_last > monaco_last, "the shorter race's last lap carries more fuel"


def test_fuel_load_never_goes_negative_past_the_modelled_distance(params: KernelParams) -> None:
    """A red-flag restart or an extra lap must not produce fuel-negative laps."""
    load = estimate_fuel_load_kg(200, 57, params)

    assert load == 0.0


def test_fuel_load_falls_back_to_the_flat_burn_rate_without_a_known_race_length(
    params: KernelParams,
) -> None:
    with_total = estimate_fuel_load_kg(10, 57, params)
    without_total = estimate_fuel_load_kg(10, None, params)

    expected = params.start_fuel_kg - params.fuel_burn_kg_per_lap * 9
    assert without_total == pytest.approx(expected)
    assert without_total != with_total


# ---------------------------------------------------------------------------
# the correction: monotonic and reversible
# ---------------------------------------------------------------------------


def test_correction_is_monotonic_in_fuel_load(params: KernelParams) -> None:
    """The same raw lap time, at a higher fuel load, must correct to a
    LOWER fuel-corrected time — more weight explains away more of the pace."""
    lighter = fuel_correction_delta_s(20.0, params)
    heavier = fuel_correction_delta_s(80.0, params)

    assert heavier > lighter > 0.0


def test_correction_is_reversible(params: KernelParams) -> None:
    """`raw = corrected + delta` must exactly recover the original time —
    the correction is a plain affine shift, not a lossy transform."""
    raw_time = 91.234
    fuel_load = 63.5

    delta = fuel_correction_delta_s(fuel_load, params)
    corrected = raw_time - delta
    recovered = corrected + delta

    assert recovered == pytest.approx(raw_time)


def test_fuel_correct_matches_the_scalar_delta_formula(params: KernelParams) -> None:
    lap_times = [90.0] * 8
    frame = _synthetic_stint_laps(lap_times)

    corrected = fuel_correct(frame, params)

    for lap_number, raw, fuel_load, got in zip(
        corrected["lap_number"],
        corrected["lap_time_s"],
        corrected["fuel_load_kg"],
        corrected["lap_time_fuel_corrected_s"],
        strict=True,
    ):
        assert fuel_load == pytest.approx(estimate_fuel_load_kg(lap_number, len(lap_times), params))
        assert got == pytest.approx(raw - fuel_correction_delta_s(fuel_load, params))


def test_fuel_correct_leaves_missing_lap_times_null(params: KernelParams) -> None:
    frame = _synthetic_stint_laps([90.0, None, 90.2, 90.1])  # type: ignore[list-item]

    corrected = fuel_correct(frame, params)

    assert corrected["lap_time_fuel_corrected_s"].null_count() == 1
    missing_row = corrected.filter(pl.col("lap_time_s").is_null())
    assert missing_row["lap_time_fuel_corrected_s"][0] is None
    # fuel_load_kg is still known even when the time is not.
    assert missing_row["fuel_load_kg"][0] is not None


# ---------------------------------------------------------------------------
# the correction flattens a fuel-driven trend
# ---------------------------------------------------------------------------


def test_fuel_correction_flattens_a_pure_fuel_trend(params: KernelParams) -> None:
    """A stint with NO degradation at all, only the fuel effect baked in at
    exactly `params.fuel_coef_s_per_kg`, must be corrected almost perfectly
    flat — the fuel trend removed entirely, not just reduced."""
    total_laps = 20
    lap_times = [
        90.0 + fuel_correction_delta_s(estimate_fuel_load_kg(lap, total_laps, params), params)
        for lap in range(1, total_laps + 1)
    ]
    frame = _synthetic_stint_laps(lap_times)

    corrected = fuel_correct(frame, params)

    raw_slope = _slope(frame["lap_number"].to_list(), frame["lap_time_s"].to_list())
    corrected_slope = _slope(
        corrected["lap_number"].to_list(), corrected["lap_time_fuel_corrected_s"].to_list()
    )
    assert abs(raw_slope) > 0.05, "the synthetic fuel trend must be real to begin with"
    assert abs(corrected_slope) < abs(raw_slope) * 0.02


def test_fuel_correction_reduces_slope_when_fuel_dominates_degradation(
    params: KernelParams,
) -> None:
    """A stint with a REALISTIC mix: a mild degradation trend plus a fuel
    effect several times larger (the normal case on a low-deg compound
    early enough in a stint) — fuel-corrected lap times must trend flatter
    than raw, not just differently."""
    total_laps = 25
    mild_deg_s_per_lap = 0.02
    lap_times = [
        90.0
        + mild_deg_s_per_lap * (lap - 1)
        + fuel_correction_delta_s(estimate_fuel_load_kg(lap, total_laps, params), params)
        for lap in range(1, total_laps + 1)
    ]
    frame = _synthetic_stint_laps(lap_times)

    corrected = fuel_correct(frame, params)

    raw_slope = _slope(frame["lap_number"].to_list(), frame["lap_time_s"].to_list())
    corrected_slope = _slope(
        corrected["lap_number"].to_list(), corrected["lap_time_fuel_corrected_s"].to_list()
    )
    assert corrected_slope == pytest.approx(mild_deg_s_per_lap, abs=1e-9)
    assert abs(corrected_slope) < abs(raw_slope)


@requires_real_backfill
def test_fuel_correction_shifts_every_real_stints_slope_by_exactly_the_fuel_component() -> None:
    """The property that holds on EVERY stint, real degradation or not (see
    `FuelStintEstimate`'s docstring for why "always flatter" is not a safe
    claim about real 2024 hard/medium tyres — degradation there can exceed
    the fuel effect's own magnitude, so correcting for fuel can reveal a
    STEEPER trend, not a flatter one): fuel correction shifts a stint's
    lap-time-vs-lap-number slope by exactly `+fuel_coef * burn_rate`,
    because the corrected series has the fuel term algebraically removed."""
    from f1_kernel.sources import list_historical_sessions

    params = KernelParams()
    source = HistoricalSource(REAL_ROOT)
    races = [
        ref.session_key
        for ref in list_historical_sessions(
            REAL_ROOT, session_types=[SessionType.RACE], seasons=[2024]
        )
    ]
    assert races
    laps = clean_laps(lap_frame(source, races[0]), params)
    corrected = fuel_correct(laps, params)
    total_laps = int(laps["lap_number"].max())  # type: ignore[arg-type]
    burn_rate = params.start_fuel_kg / total_laps

    checked = 0
    for (_driver, _stint), group in corrected.group_by(["driver", "stint_number"]):
        if group.height < 6:
            continue
        ordered = group.sort("lap_number")
        raw_slope = _slope(ordered["lap_number"].to_list(), ordered["lap_time_s"].to_list())
        corrected_slope = _slope(
            ordered["lap_number"].to_list(), ordered["lap_time_fuel_corrected_s"].to_list()
        )
        assert corrected_slope - raw_slope == pytest.approx(
            params.fuel_coef_s_per_kg * burn_rate, abs=1e-6
        )
        checked += 1
    assert checked >= 5


# ---------------------------------------------------------------------------
# empty / error handling, matching the cleaning module's conventions
# ---------------------------------------------------------------------------


def test_fuel_correct_on_an_empty_frame_returns_an_empty_frame(params: KernelParams) -> None:
    empty = pl.DataFrame(schema=LAP_FRAME_SCHEMA)

    corrected = fuel_correct(empty, params)

    assert corrected.is_empty()
    assert {"fuel_load_kg", "lap_time_fuel_corrected_s"} <= set(corrected.columns)


def test_fuel_correct_rejects_a_frame_missing_required_columns(params: KernelParams) -> None:
    with pytest.raises(ValueError, match="missing required column"):
        fuel_correct(pl.DataFrame({"driver": ["1"]}), params)


def test_fuel_correct_is_deterministic(source: HistoricalSource, params: KernelParams) -> None:
    laps = clean_laps(lap_frame(source, SC_RACE), params)

    first = fuel_correct(laps, params)
    second = fuel_correct(laps, params)

    assert first.equals(second)


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


@requires_real_backfill
def test_calibration_over_real_races_is_physically_plausible() -> None:
    """The real backtest for this prompt: run the calibration over the real
    2023-2025 corpus (whatever is locally backfilled) and check the fitted
    coefficient's sign and rough magnitude — a few hundredths of a second
    per kilogram, per publicly quoted F1 figures."""
    from f1_kernel.sources import list_historical_sessions

    source = HistoricalSource(REAL_ROOT)
    sessions = [
        ref.session_key
        for ref in list_historical_sessions(REAL_ROOT, session_types=[SessionType.RACE])
    ]
    assert sessions, "expected at least one backfilled race"

    result = calibrate_fuel_coefficient(source, sessions, KernelParams())

    assert result.n_stints >= 10
    assert result.fuel_coef_s_per_kg > 0, "more fuel must cost time, not save it"
    assert 0.005 < result.fuel_coef_s_per_kg < 0.15
    assert result.ci_low_s_per_kg <= result.fuel_coef_s_per_kg <= result.ci_high_s_per_kg


def test_calibration_raises_when_nothing_qualifies() -> None:
    """A calibration that silently produced nothing would be worse than one
    that says so — an all-soft, too-short synthetic race qualifies for
    nothing on hard/medium."""
    laps = _synthetic_stint_laps([90.0, 90.1, 89.9])  # only 3 laps, below min_stint_laps

    with pytest.raises(ValueError, match="no stint"):
        calibrate_fuel_coefficient_from_frames({SC_RACE: laps}, KernelParams())


def test_calibration_raises_without_cross_circuit_variation() -> None:
    """`fuel_coef_s_per_kg` is identified by DIFFERENT races having
    different burn rates. Every qualifying stint coming from same-length
    races (here, literally the same race twice) must not silently produce
    a meaningless number."""
    laps = _synthetic_stint_laps([90.0 + 0.01 * lap for lap in range(20)], compound="hard")
    other_key = SessionKey(year=2024, round=2, session_type=SessionType.RACE)

    with pytest.raises(ValueError, match="same length"):
        calibrate_fuel_coefficient_from_frames(
            {SC_RACE: laps, other_key: laps}, KernelParams(), min_stint_laps=5
        )


def test_calibration_recovers_a_known_coefficient_from_synthetic_stints() -> None:
    """A closed-loop check: bake `fuel_coef_s_per_kg` into synthetic
    stints — zero degradation, so the whole raw slope is the fuel
    component — across races of DIFFERENT lengths (hence different burn
    rates, the source of identification), and confirm the calibration
    recovers the true coefficient, independent of whatever real data is or
    isn't available locally."""
    true_coef = 0.04
    fitted_params = KernelParams().with_overrides(fuel_coef_s_per_kg=true_coef)
    session_lengths = {1: 40, 2: 55, 3: 70, 4: 30}
    frames: dict[SessionKey, pl.DataFrame] = {}
    for round_no, total_laps in session_lengths.items():
        lap_times = [
            90.0
            + fuel_correction_delta_s(
                estimate_fuel_load_kg(lap, total_laps, fitted_params), fitted_params
            )
            for lap in range(1, total_laps + 1)
        ]
        key = SessionKey(year=2024, round=round_no, session_type=SessionType.RACE)
        frames[key] = _synthetic_stint_laps(lap_times, compound="hard")

    result = calibrate_fuel_coefficient_from_frames(frames, KernelParams(), min_stint_laps=5)

    assert result.fuel_coef_s_per_kg == pytest.approx(true_coef, rel=0.01)
    assert result.n_stints == len(session_lengths)
    assert result.n_sessions == len(session_lengths)
    assert result.compounds_used == ("hard",)
    assert result.ci_low_s_per_kg <= result.fuel_coef_s_per_kg <= result.ci_high_s_per_kg


def test_calibration_is_deterministic() -> None:
    session_lengths = {1: 40, 2: 55, 3: 70}
    frames: dict[SessionKey, pl.DataFrame] = {
        SessionKey(year=2024, round=round_no, session_type=SessionType.RACE): _synthetic_stint_laps(
            [90.0 - 0.02 * lap for lap in range(total_laps)], compound="hard"
        )
        for round_no, total_laps in session_lengths.items()
    }

    first = calibrate_fuel_coefficient_from_frames(frames, KernelParams(), min_stint_laps=5)
    second = calibrate_fuel_coefficient_from_frames(frames, KernelParams(), min_stint_laps=5)

    assert first.fuel_coef_s_per_kg == second.fuel_coef_s_per_kg
    assert first.ci_low_s_per_kg == second.ci_low_s_per_kg
    assert first.ci_high_s_per_kg == second.ci_high_s_per_kg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _slope(x: list[float], y: list[float | None]) -> float:
    """OLS slope, ignoring any null y — good enough for a test assertion."""
    pairs = [(xi, yi) for xi, yi in zip(x, y, strict=True) if yi is not None]
    n = len(pairs)
    if n < 2:
        return 0.0
    mean_x = sum(p[0] for p in pairs) / n
    mean_y = sum(p[1] for p in pairs) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in pairs)
    den = sum((xi - mean_x) ** 2 for xi, _ in pairs)
    return num / den if den else 0.0


def _synthetic_stint_laps(
    lap_times: Sequence[float | None], *, compound: str = "medium"
) -> pl.DataFrame:
    """A minimal one-driver, one-stint frame with real lap numbers/session
    identity, for tests that only care about the fuel model."""
    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    rows: dict[str, list[object]] = {name: [] for name in LAP_FRAME_SCHEMA}
    clock = start
    for index, lap_time in enumerate(lap_times, start=1):
        duration = lap_time if lap_time is not None else 90.0
        end = clock + timedelta(seconds=duration)
        rows["session_year"].append(2024)
        rows["session_round"].append(1)
        rows["session_type"].append("race")
        rows["driver"].append("1")
        rows["lap_number"].append(index)
        rows["lap_start_utc"].append(clock)
        rows["lap_end_utc"].append(end)
        rows["lap_time_s"].append(lap_time)
        rows["sector1_s"].append(None)
        rows["sector2_s"].append(None)
        rows["sector3_s"].append(None)
        rows["is_personal_best"].append(False)
        rows["compound"].append(compound)
        rows["tyre_age"].append(index)
        rows["stint_number"].append(1)
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
    return pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA)
