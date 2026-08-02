"""Braking-point drift: `braking_points` recovers a planted sustained
crossing (and rejects a bare stab), corner windows stay anchored to real
circuit geometry and do not leak into a neighbour's zone, and
`braking_drift` recovers a known trend across tyre age with the same
`thin`/`unreliable` honesty `fit_degradation` uses.

CLAUDE.md's framing for this prompt: this is the thing the project
originally thought needed a camera, done from the brake channel every lap
instead. The end-to-end test at the bottom builds a synthetic "long stint on
a fading brake" telemetry trace and drives it through BOTH stages
(`braking_points` then `braking_drift`), because the prompt's actual
deliverable is the aggregate DIRECTION the two compose to, not either
function in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from f1_kernel import (
    BRAKING_DRIFT_SCHEMA,
    BRAKING_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    TELEMETRY_FRAME_SCHEMA,
    Circuit,
    Corner,
    KernelParams,
    Reliability,
    braking_drift,
    braking_drift_to_frame,
    braking_points,
    load_circuit,
)

START = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)

REAL_ROOT = Path(__file__).parents[3] / "data" / "historical"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _telemetry_frame(
    samples_by_lap: dict[int, list[tuple[float, float]]], *, driver: str = "1"
) -> pl.DataFrame:
    """One driver's telemetry, built directly from `{lap_number:
    [(lap_distance_m, brake_pct), ...]}` in the exact order given — a test
    wants the points on the brake trace, not a simulated lap."""
    rows: dict[str, list[object]] = {name: [] for name in TELEMETRY_FRAME_SCHEMA}
    clock = START
    distance = 0.0
    for lap_number in sorted(samples_by_lap):
        for lap_distance, brake in samples_by_lap[lap_number]:
            clock += timedelta(seconds=0.2)
            distance += 1.0
            rows["session_year"].append(2024)
            rows["session_round"].append(1)
            rows["session_type"].append("race")
            rows["driver"].append(driver)
            rows["ts_utc"].append(clock)
            rows["time_s"].append(0.0)
            rows["lap_number"].append(lap_number)
            rows["distance_m"].append(distance)
            rows["lap_distance_m"].append(lap_distance)
            rows["speed_kph"].append(200.0)
            rows["throttle_pct"].append(0.0)
            rows["brake_pct"].append(brake)
            rows["gear"].append(3)
            rows["rpm"].append(9000)
            rows["drs"].append(0)
            rows["drs_open"].append(False)
            rows["provenance"].append("historical")
    return pl.DataFrame(rows, schema=TELEMETRY_FRAME_SCHEMA)


def _sweep(
    max_distance: float, step: float, on_ranges: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Every `step` metres up to `max_distance`; brake ON (100%) inside any
    half-open `[start, end)` of `on_ranges`, OFF (0%) elsewhere."""
    samples: list[tuple[float, float]] = []
    distance = 0.0
    while distance <= max_distance:
        on = any(start <= distance < end for start, end in on_ranges)
        samples.append((distance, 100.0 if on else 0.0))
        distance += step
    return samples


def _circuit(corners: list[Corner], *, lap_distance_m: float = 1000.0) -> Circuit:
    return Circuit(
        circuit_id="test",
        name="Test Circuit",
        country="Testland",
        event_names=["Test Grand Prix"],
        lap_distance_m=lap_distance_m,
        pit_loss_s=20.0,
        corners=corners,
    )


def _stint_row(
    *,
    driver: str = "1",
    stint_number: int = 1,
    compound: str = "hard",
    start_lap: int,
    end_lap: int,
    tyre_age_at_start: int = 0,
) -> dict[str, object]:
    return {
        "session_year": 2024,
        "session_round": 1,
        "session_type": "race",
        "driver": driver,
        "stint_number": stint_number,
        "compound": compound,
        "start_lap": start_lap,
        "end_lap": end_lap,
        "lap_count": end_lap - start_lap + 1,
        "tyre_age_at_start": tyre_age_at_start,
        "provenance": "historical",
    }


def _stint_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=STINT_FRAME_SCHEMA)


def _braking_row(
    *,
    driver: str = "1",
    lap_number: int,
    corner_number: int = 1,
    corner_letter: str | None = None,
    corner_distance_m: float = 500.0,
    distance_before_corner_m: float | None,
    known: bool = True,
    n_samples: int = 20,
) -> dict[str, object]:
    brake_point = (
        corner_distance_m - distance_before_corner_m
        if distance_before_corner_m is not None
        else None
    )
    return {
        "session_year": 2024,
        "session_round": 1,
        "session_type": "race",
        "driver": driver,
        "lap_number": lap_number,
        "corner_number": corner_number,
        "corner_letter": corner_letter,
        "corner_distance_m": corner_distance_m,
        "brake_point_distance_m": brake_point,
        "distance_before_corner_m": distance_before_corner_m,
        "n_samples_in_window": n_samples,
        "known": known,
    }


def _braking_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=BRAKING_FRAME_SCHEMA)


# ---------------------------------------------------------------------------
# braking_points: detection
# ---------------------------------------------------------------------------


def test_braking_points_finds_the_sustained_crossing(params: KernelParams) -> None:
    corner = Corner(number=1, letter=None, distance_m=500.0)
    telemetry = _telemetry_frame({1: _sweep(600.0, 5.0, [(400.0, 465.0)])})

    frame = braking_points(telemetry, _circuit([corner]), params)

    assert frame.height == 1
    row = frame.row(0, named=True)
    assert row["known"] is True
    assert row["brake_point_distance_m"] == pytest.approx(400.0, abs=1e-9)
    assert row["distance_before_corner_m"] == pytest.approx(100.0, abs=1e-9)
    assert row["n_samples_in_window"] == 51  # every 5m from 250 to 500 inclusive


def test_a_brief_stab_is_not_a_sustained_braking_point(params: KernelParams) -> None:
    """One noisy sample above threshold, surrounded by off samples, must not
    register as a braking point — it is a stab, not sustained application."""
    corner = Corner(number=1, letter=None, distance_m=500.0)
    telemetry = _telemetry_frame({1: _sweep(600.0, 5.0, [(400.0, 405.0)])})

    frame = braking_points(telemetry, _circuit([corner]), params)

    row = frame.row(0, named=True)
    assert row["known"] is True, "plenty of telemetry fell in the window"
    assert row["brake_point_distance_m"] is None
    assert row["distance_before_corner_m"] is None


def test_too_few_samples_in_window_is_unknown_even_with_a_run(params: KernelParams) -> None:
    """Two adjacent on-samples would otherwise form a qualifying run, but
    with only two samples total in the window there is not enough telemetry
    to trust ANY verdict — `known` must gate ahead of the run detection."""
    assert params.braking_min_samples_in_window > 2
    corner = Corner(number=1, letter=None, distance_m=500.0)
    telemetry = _telemetry_frame({1: [(400.0, 100.0), (405.0, 100.0)]})

    frame = braking_points(telemetry, _circuit([corner]), params)

    row = frame.row(0, named=True)
    assert row["n_samples_in_window"] == 2
    assert row["known"] is False
    assert row["brake_point_distance_m"] is None


def test_a_corners_search_window_does_not_leak_into_the_previous_corners_zone(
    params: KernelParams,
) -> None:
    """A chicane: corner 2 sits only 20m after corner 1. Without clipping,
    corner 2's default ~250m approach window would reach back into corner
    1's own braking zone and steal its event; clipped at corner 1's own
    distance, corner 2 must find its OWN local braking point instead."""
    corner1 = Corner(number=1, letter=None, distance_m=300.0)
    corner2 = Corner(number=2, letter=None, distance_m=320.0)
    telemetry = _telemetry_frame({1: _sweep(400.0, 5.0, [(250.0, 270.0), (305.0, 320.0)])})

    frame = braking_points(telemetry, _circuit([corner1, corner2]), params)

    by_corner = {int(row["corner_number"]): row for row in frame.to_dicts()}
    assert by_corner[1]["known"] is True
    assert 250.0 <= by_corner[1]["brake_point_distance_m"] < 300.0
    assert by_corner[2]["known"] is True
    assert by_corner[2]["brake_point_distance_m"] == pytest.approx(305.0, abs=1e-9), (
        "corner 2 must land on its own local event (305m), not corner 1's (250m)"
    )


def test_braking_points_lands_near_a_real_mapped_circuits_corner() -> None:
    """Corner detection is anchored to REAL shipped circuit geometry
    (Bahrain corner 1) even though the telemetry itself is synthetic."""
    circuit = load_circuit("bahrain")
    corner1 = min(circuit.corners, key=lambda corner: corner.distance_m)
    telemetry = _telemetry_frame({1: _sweep(corner1.distance_m + 100.0, 5.0, [(600.0, 650.0)])})

    frame = braking_points(telemetry, circuit, KernelParams())

    row = frame.filter(pl.col("corner_number") == corner1.number).row(0, named=True)
    assert row["known"] is True
    assert row["brake_point_distance_m"] == pytest.approx(600.0, abs=1e-9)
    assert row["distance_before_corner_m"] == pytest.approx(corner1.distance_m - 600.0, abs=1e-9)


def test_braking_points_is_deterministic(params: KernelParams) -> None:
    corner = Corner(number=1, letter=None, distance_m=500.0)
    telemetry = _telemetry_frame({1: _sweep(600.0, 5.0, [(400.0, 460.0)])})
    circuit = _circuit([corner])

    first = braking_points(telemetry, circuit, params)
    second = braking_points(telemetry, circuit, params)

    assert first.equals(second)


# ---------------------------------------------------------------------------
# braking_points: graceful degradation
# ---------------------------------------------------------------------------


def test_braking_points_with_no_circuit_is_empty(params: KernelParams) -> None:
    telemetry = _telemetry_frame({1: _sweep(600.0, 5.0, [(400.0, 460.0)])})

    frame = braking_points(telemetry, None, params)

    assert frame.is_empty()
    assert dict(frame.schema) == BRAKING_FRAME_SCHEMA


def test_braking_points_with_no_corners_is_empty(params: KernelParams) -> None:
    telemetry = _telemetry_frame({1: _sweep(600.0, 5.0, [(400.0, 460.0)])})

    frame = braking_points(telemetry, _circuit([]), params)

    assert frame.is_empty()


def test_braking_points_on_empty_telemetry_is_empty(params: KernelParams) -> None:
    corner = Corner(number=1, letter=None, distance_m=500.0)

    frame = braking_points(pl.DataFrame(schema=TELEMETRY_FRAME_SCHEMA), _circuit([corner]), params)

    assert frame.is_empty()


def test_braking_points_rejects_a_frame_missing_required_columns(params: KernelParams) -> None:
    corner = Corner(number=1, letter=None, distance_m=500.0)
    with pytest.raises(ValueError, match="missing required column"):
        braking_points(pl.DataFrame({"driver": ["1"]}), _circuit([corner]), params)


# ---------------------------------------------------------------------------
# braking_drift: recovering a known trend
# ---------------------------------------------------------------------------


def test_braking_drift_recovers_a_known_linear_trend(params: KernelParams) -> None:
    true_rate = 2.0
    true_intercept = 80.0
    rows = [
        _braking_row(lap_number=age, distance_before_corner_m=true_intercept + true_rate * age)
        for age in range(1, 11)
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=10)])

    fits = braking_drift(_braking_frame(rows), stints, params)

    assert len(fits) == 1
    fit = fits[0]
    assert fit.driver == "1"
    assert fit.stint_number == 1
    assert fit.compound == "hard"
    assert fit.corner_number == 1
    assert fit.corner_letter is None
    assert fit.drift_m_per_lap == pytest.approx(true_rate, abs=1e-9)
    assert fit.intercept_m == pytest.approx(true_intercept, abs=1e-9)
    assert fit.n_laps_used == 10
    assert fit.reliability is Reliability.GOOD


def test_braking_drift_reliability_is_thin_below_the_minimum_lap_count(
    params: KernelParams,
) -> None:
    assert params.min_laps_for_braking_drift > 3
    rows = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 2.0 * age)
        for age in range(1, 4)
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=3)])

    fits = braking_drift(_braking_frame(rows), stints, params)

    assert len(fits) == 1
    assert fits[0].n_laps_used == 3
    assert fits[0].reliability is Reliability.THIN


def test_braking_drift_reliability_is_unreliable_when_the_ci_is_too_wide(
    params: KernelParams,
) -> None:
    n_laps = params.min_laps_for_braking_drift + 3
    rows = [
        _braking_row(
            lap_number=age,
            distance_before_corner_m=80.0 + (30.0 if age % 2 == 0 else -30.0),
        )
        for age in range(1, n_laps + 1)
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=n_laps)])

    fits = braking_drift(_braking_frame(rows), stints, params)

    assert len(fits) == 1
    fit = fits[0]
    assert fit.n_laps_used >= params.min_laps_for_braking_drift
    width = fit.drift_ci_high_m_per_lap - fit.drift_ci_low_m_per_lap
    assert width > params.braking_drift_max_ci_width_m_per_lap
    assert fit.reliability is Reliability.UNRELIABLE


def test_braking_drift_excludes_unknown_and_no_detection_rows(params: KernelParams) -> None:
    good_rows = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 2.0 * age)
        for age in range(1, 7)
    ]
    excluded_rows = [
        _braking_row(lap_number=7, distance_before_corner_m=None, known=True),  # no run found
        _braking_row(lap_number=8, distance_before_corner_m=200.0, known=False),  # unknown
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=8)])

    fits = braking_drift(_braking_frame(good_rows + excluded_rows), stints, params)

    assert len(fits) == 1
    assert fits[0].n_laps_used == 6
    assert fits[0].drift_m_per_lap == pytest.approx(2.0, abs=1e-9)


def test_braking_drift_excludes_laps_outside_any_stint(params: KernelParams) -> None:
    rows = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 2.0 * age)
        for age in range(1, 6)
    ] + [_braking_row(lap_number=15, distance_before_corner_m=500.0)]  # far outside the stint
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=5)])

    fits = braking_drift(_braking_frame(rows), stints, params)

    assert len(fits) == 1
    assert fits[0].n_laps_used == 5
    assert fits[0].drift_m_per_lap == pytest.approx(2.0, abs=1e-9)


def test_braking_drift_resets_tyre_age_and_separates_fits_per_stint(
    params: KernelParams,
) -> None:
    stint1 = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 1.0 * age)
        for age in range(1, 8)
    ]
    stint2 = [
        _braking_row(lap_number=7 + age, distance_before_corner_m=70.0 + 3.0 * age)
        for age in range(1, 8)
    ]
    stints = _stint_frame(
        [
            _stint_row(stint_number=1, compound="medium", start_lap=1, end_lap=7),
            _stint_row(stint_number=2, compound="hard", start_lap=8, end_lap=14),
        ]
    )

    fits = braking_drift(_braking_frame(stint1 + stint2), stints, params)

    by_stint = {fit.stint_number: fit for fit in fits}
    assert set(by_stint) == {1, 2}
    assert by_stint[1].compound == "medium"
    assert by_stint[1].drift_m_per_lap == pytest.approx(1.0, abs=1e-9)
    assert by_stint[2].compound == "hard"
    assert by_stint[2].drift_m_per_lap == pytest.approx(3.0, abs=1e-9)


def test_braking_drift_skips_a_group_with_no_tyre_age_variation(params: KernelParams) -> None:
    rows = [_braking_row(lap_number=1, distance_before_corner_m=80.0)]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=1)])

    assert braking_drift(_braking_frame(rows), stints, params) == []


def test_braking_drift_on_empty_frames_returns_empty_list(params: KernelParams) -> None:
    empty_braking = pl.DataFrame(schema=BRAKING_FRAME_SCHEMA)
    empty_stints = pl.DataFrame(schema=STINT_FRAME_SCHEMA)
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=5)])
    rows = _braking_frame(
        [_braking_row(lap_number=age, distance_before_corner_m=80.0 + age) for age in range(1, 6)]
    )

    assert braking_drift(empty_braking, stints, params) == []
    assert braking_drift(rows, empty_stints, params) == []


def test_braking_drift_rejects_frames_missing_required_columns(params: KernelParams) -> None:
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=5)])
    with pytest.raises(ValueError, match="missing required column"):
        braking_drift(pl.DataFrame({"driver": ["1"]}), stints, params)

    rows = _braking_frame(
        [_braking_row(lap_number=age, distance_before_corner_m=80.0 + age) for age in range(1, 6)]
    )
    with pytest.raises(ValueError, match="missing required column"):
        braking_drift(rows, pl.DataFrame({"driver": ["1"]}), params)


def test_braking_drift_is_deterministic(params: KernelParams) -> None:
    rows = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 2.0 * age)
        for age in range(1, 10)
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=9)])

    first = braking_drift(_braking_frame(rows), stints, params)
    second = braking_drift(_braking_frame(rows), stints, params)

    assert first == second


def test_braking_drift_to_frame_round_trips_the_key_fields(params: KernelParams) -> None:
    rows = [
        _braking_row(lap_number=age, distance_before_corner_m=80.0 + 2.0 * age)
        for age in range(1, 10)
    ]
    stints = _stint_frame([_stint_row(start_lap=1, end_lap=9)])
    fits = braking_drift(_braking_frame(rows), stints, params)

    table = braking_drift_to_frame(fits)

    assert dict(table.schema) == BRAKING_DRIFT_SCHEMA
    assert table.height == 1
    assert table["drift_m_per_lap"][0] == pytest.approx(fits[0].drift_m_per_lap)
    assert table["reliability"][0] == fits[0].reliability.value


def test_braking_drift_to_frame_on_no_fits_is_empty() -> None:
    table = braking_drift_to_frame([])
    assert table.is_empty()
    assert dict(table.schema) == BRAKING_DRIFT_SCHEMA


# ---------------------------------------------------------------------------
# end-to-end: a long stint on a fading brake, the prompt's actual test
# ---------------------------------------------------------------------------


def test_a_long_high_deg_stint_shows_measurable_earlier_braking_drift(
    params: KernelParams,
) -> None:
    """The full pipeline, `braking_points` into `braking_drift`: a
    heavy-braking corner whose true brake-application point moves 4m
    earlier every lap of a 15-lap stint (simulating fading brakes/tyres on
    a high-deg compound). The fitted drift must recover that direction on
    aggregate — the actual deliverable of this prompt, not either function
    in isolation."""
    corner = Corner(number=1, letter=None, distance_m=500.0)
    drift_per_lap = 4.0
    samples_by_lap = {
        lap: _sweep(600.0, 2.0, [(400.0 - drift_per_lap * (lap - 1), 460.0)])
        for lap in range(1, 16)
    }
    telemetry = _telemetry_frame(samples_by_lap)
    points = braking_points(telemetry, _circuit([corner]), params)
    assert (points["known"]).all()
    assert points["distance_before_corner_m"].null_count() == 0

    stints = _stint_frame([_stint_row(start_lap=1, end_lap=15, compound="hard")])
    fits = braking_drift(points, stints, params)

    assert len(fits) == 1
    fit = fits[0]
    assert fit.reliability is Reliability.GOOD
    assert fit.drift_m_per_lap == pytest.approx(drift_per_lap, abs=0.5), (
        "the brake point must be recovered as moving EARLIER (positive "
        "drift) as tyre age increases, matching the planted trend"
    )
    assert fit.drift_m_per_lap > 0


# ---------------------------------------------------------------------------
# real-data (opt-in, skipped without a local backfill with telemetry)
# ---------------------------------------------------------------------------


def _real_bahrain_available() -> bool:
    from f1_domain import SessionKey, SessionType
    from f1_kernel.sources import list_historical_sessions

    if not REAL_ROOT.is_dir():
        return False
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    return any(ref.session_key == key for ref in list_historical_sessions(REAL_ROOT))


@pytest.mark.skipif(
    not _real_bahrain_available(),
    reason="2024 Bahrain race not backfilled; run `uv run f1-backfill --season 2024`",
)
def test_real_braking_drift_direction_on_a_long_stint() -> None:
    """Over a real driver's longest stint at a real, mapped circuit, the
    reliable corner fits should skew towards earlier braking on aggregate
    rather than later — a weak, noise-tolerant version of the synthetic
    test above, included because the prompt asks for the real-telemetry
    case specifically. Skips (rather than fails) when the local backfill
    has too little reliable evidence to judge, since real telemetry
    coverage is exactly the kind of thing CLAUDE.md says varies."""
    from f1_domain import SessionKey, SessionType
    from f1_kernel import HistoricalSource, circuit_for_event, stint_frame, telemetry_frame
    from f1_kernel.sources import list_historical_sessions

    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    source = HistoricalSource(REAL_ROOT)
    event_name = next(
        (ref.event_name for ref in list_historical_sessions(REAL_ROOT) if ref.session_key == key),
        None,
    )
    circuit = circuit_for_event(event_name)
    if circuit is None:
        pytest.skip("2024 Bahrain has no shipped circuit file to anchor corners to")

    stints = stint_frame(source, key)
    if stints.is_empty():
        pytest.skip("no stint data available")
    longest = stints.sort("lap_count", descending=True).row(0, named=True)
    driver = str(longest["driver"])

    telemetry = telemetry_frame(source, key, driver)
    if telemetry.is_empty():
        pytest.skip("no telemetry recorded for this backfill")

    points = braking_points(telemetry, circuit, KernelParams())
    fits = braking_drift(points, stints.filter(pl.col("driver") == driver), KernelParams())
    reliable = [fit for fit in fits if fit.reliability is not Reliability.THIN]
    if len(reliable) < 3:
        pytest.skip("too few reliable corner fits in this backfill to judge a direction")

    median_drift = sorted(fit.drift_m_per_lap for fit in reliable)[len(reliable) // 2]
    assert median_drift > -2.0, (
        "reliable corner fits should not skew systematically towards LATER "
        "braking as the stint wears on"
    )
