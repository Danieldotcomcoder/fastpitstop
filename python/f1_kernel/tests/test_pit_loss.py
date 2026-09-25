"""Pit-loss measurement: a planted stop is recovered exactly, every
disqualifying condition (safety car, drive-through, missing neighbours)
lands in the exclusion ledger instead of silently vanishing, and — over
the real backfill — the calibrated medians land where the physics says
they must (Spa cheap, Bahrain dear, everything in a plausible band)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

import polars as pl
import pytest

from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource, KernelParams, calibrate_pit_loss
from f1_kernel.fuel import estimate_fuel_load_kg, fuel_correction_delta_s
from f1_kernel.pit_loss import pit_loss_samples
from f1_kernel.schemas import LAP_FRAME_SCHEMA

RACE_START = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)

REAL_ROOT = Path(__file__).parents[3] / "data" / "historical"


def _real_races_available(*keys: SessionKey) -> bool:
    from f1_kernel.sources import list_historical_sessions

    if not REAL_ROOT.is_dir():
        return False
    available = {ref.session_key for ref in list_historical_sessions(REAL_ROOT)}
    return all(key in available for key in keys)


BAHRAIN_2024 = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
SPA_2024 = SessionKey(year=2024, round=14, session_type=SessionType.RACE)


def _stop_frame(
    *,
    driver: str = "1",
    in_lap: int = 10,
    total_laps: int = 20,
    baseline_s: float = 90.0,
    in_lap_s: float = 100.0,
    out_lap_s: float = 95.0,
    post_baseline_s: float = 89.0,
    stop_status: str = "green",
    stint_change: bool = True,
) -> pl.DataFrame:
    """One driver, one stop: `baseline_s` laps before the in-lap,
    `post_baseline_s` laps after the out-lap."""
    rows: list[dict[str, object]] = []
    clock = RACE_START
    for lap in range(1, total_laps + 1):
        if lap == in_lap:
            lap_time = in_lap_s
        elif lap == in_lap + 1:
            lap_time = out_lap_s
        elif lap <= in_lap:
            lap_time = baseline_s
        else:
            lap_time = post_baseline_s
        end = clock + timedelta(seconds=lap_time)
        in_new_stint = stint_change and lap > in_lap
        status = stop_status if lap in (in_lap, in_lap + 1) else "green"
        rows.append(
            {
                "session_year": 2024,
                "session_round": 1,
                "session_type": "race",
                "driver": driver,
                "lap_number": lap,
                "lap_start_utc": clock,
                "lap_end_utc": end,
                "lap_time_s": lap_time,
                "sector1_s": None,
                "sector2_s": None,
                "sector3_s": None,
                "is_personal_best": False,
                "compound": "hard" if in_new_stint else "medium",
                "tyre_age": lap - in_lap if in_new_stint else lap,
                "stint_number": 2 if in_new_stint else 1,
                "position": None,
                "gap_ahead_s": None,
                "gap_ahead_min_s": None,
                "gap_ahead_samples": 0,
                "gap_to_leader_s": None,
                "track_status": status,
                "track_status_flags": [] if status == "green" else [status],
                "is_pit_in": lap == in_lap,
                "is_pit_out": lap == in_lap + 1,
                "provenance": "historical",
            }
        )
        clock = end
    return pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA)


def _expected_loss(
    params: KernelParams,
    *,
    in_lap: int,
    total_laps: int,
    baseline_s: float,
    in_lap_s: float,
    out_lap_s: float,
    post_baseline_s: float,
    pre_laps: list[int],
    post_laps: list[int],
) -> float:
    """The planted loss, fuel-corrected with the same public helpers the
    module uses — the test computes its expectation independently."""

    def corrected(raw: float, lap: int) -> float:
        load = estimate_fuel_load_kg(lap, total_laps, params)
        return raw - fuel_correction_delta_s(load, params)

    pre_median = median(corrected(baseline_s, lap) for lap in pre_laps)
    post_median = median(corrected(post_baseline_s, lap) for lap in post_laps)
    return (
        corrected(in_lap_s, in_lap) + corrected(out_lap_s, in_lap + 1) - (pre_median + post_median)
    )


def test_a_planted_green_stop_is_measured_exactly(params: KernelParams) -> None:
    frame = _stop_frame()

    samples, excluded = pit_loss_samples(frame, params)

    assert excluded == {}
    assert len(samples) == 1
    sample = samples[0]
    assert sample.driver == "1"
    assert sample.in_lap == 10
    assert sample.out_lap == 11
    # Neighbour windows: clean pre laps 5-9 (lap 1 excluded anyway), clean
    # post laps 12-16.
    expected = _expected_loss(
        params,
        in_lap=10,
        total_laps=20,
        baseline_s=90.0,
        in_lap_s=100.0,
        out_lap_s=95.0,
        post_baseline_s=89.0,
        pre_laps=[5, 6, 7, 8, 9],
        post_laps=[12, 13, 14, 15, 16],
    )
    assert sample.pit_loss_s == pytest.approx(expected, abs=1e-9)
    assert sample.n_pre_laps == 5
    assert sample.n_post_laps == 5


def test_a_stop_under_the_safety_car_is_excluded_and_counted(params: KernelParams) -> None:
    frame = _stop_frame(stop_status="sc")

    samples, excluded = pit_loss_samples(frame, params)

    assert samples == []
    assert excluded["not_green"] == 1


def test_a_drive_through_without_a_stint_change_is_excluded(params: KernelParams) -> None:
    frame = _stop_frame(stint_change=False)

    samples, excluded = pit_loss_samples(frame, params)

    assert samples == []
    assert excluded["no_stint_change"] == 1


def test_a_stop_with_too_few_neighbours_is_excluded(params: KernelParams) -> None:
    # In-lap 2: the only pre-window candidate is lap 1, which the clean-lap
    # filter always removes -> no pre baseline.
    frame = _stop_frame(in_lap=2)

    samples, excluded = pit_loss_samples(frame, params)

    assert samples == []
    assert excluded["too_few_pre_laps"] == 1


def test_an_implausible_measurement_is_excluded(params: KernelParams) -> None:
    frame = _stop_frame(in_lap_s=190.0)  # a ~100s "stop": red-flag debris, not a pit stop

    samples, excluded = pit_loss_samples(frame, params)

    assert samples == []
    assert excluded["implausible"] == 1


def test_pit_loss_samples_are_deterministic(params: KernelParams) -> None:
    frame = _stop_frame()

    first = pit_loss_samples(frame, params)
    second = pit_loss_samples(frame, params)

    assert first == second


def test_an_empty_frame_measures_nothing(params: KernelParams) -> None:
    samples, excluded = pit_loss_samples(pl.DataFrame(schema=LAP_FRAME_SCHEMA), params)

    assert samples == []
    assert excluded == {}


# ---------------------------------------------------------------------------
# real-data calibration (opt-in, skipped without a local backfill)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _real_races_available(BAHRAIN_2024, SPA_2024),
    reason="2024 Bahrain + Belgian races not backfilled; run `uv run f1-backfill --season 2024`",
)
def test_real_pit_loss_ranks_spa_below_bahrain(params: KernelParams) -> None:
    """The one ordering everyone in the paddock agrees on: Spa has the
    cheapest stop on the calendar (short pit lane relative to a 7km lap),
    Bahrain one of the dearer ones. Both must also sit in the plausible
    total-loss band — this is what catches a broken baseline or a stop
    filter that silently stopped filtering."""
    source = HistoricalSource(REAL_ROOT)

    result = calibrate_pit_loss(source, [BAHRAIN_2024, SPA_2024], params)

    by_key = {estimate.circuit_key: estimate for estimate in result.circuits}
    # Keyed by circuit id now that Spa ships a circuit file (it was keyed by
    # its Grand Prix name, "belgian_grand_prix", while it had none).
    assert {"bahrain", "spa"} <= set(by_key)
    spa, bahrain = by_key["spa"], by_key["bahrain"]
    assert spa.n_stops >= 10 and bahrain.n_stops >= 10
    assert spa.pit_loss_s < bahrain.pit_loss_s
    for estimate in (spa, bahrain):
        assert 15.0 < estimate.pit_loss_s < 35.0
        assert 0.0 < estimate.uncertainty_s < 2.0
