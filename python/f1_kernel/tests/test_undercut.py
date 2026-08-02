"""Undercut/overcut crossover: the projection arithmetic against
hand-computed cases, the honesty machinery (UNKNOWN, confidence caps), and
the pit-loss-cancellation invariant the module docstring promises.

The projection tests construct `DegradationFit`s directly instead of
fitting them — `fit_degradation`'s recovery of known rates is already
covered in test_degradation.py, and handing the analytic exact fits is
what makes every expected margin below computable by hand:

    margin(L) = -gap - A_old(1) + B_fresh(1)
                + sum_{k=1..L} (B_old(k) - A_fresh(k))        [undercut]

with pace(age) = intercept + rate * age, A_fresh(j) at age j, and L the
stagger between the decision lap and the defender's in-lap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from f1_domain import SessionKey, SessionType
from f1_kernel import (
    Confidence,
    CrossoverKind,
    DegradationFit,
    KernelParams,
    Reliability,
    SessionFrames,
    WindowState,
    crossover_results_to_frame,
    overcut_window,
    undercut_snapshot,
    undercut_window,
)
from f1_kernel.schemas import LAP_FRAME_SCHEMA, STINT_FRAME_SCHEMA, empty_frame

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
RACE_START = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)


def _fit(
    driver: str,
    *,
    rate: float,
    intercept: float = 90.0,
    stint: int = 1,
    compound: str = "medium",
    ci_low: float | None = None,
    ci_high: float | None = None,
    n_laps: int = 10,
    reliability: Reliability = Reliability.GOOD,
) -> DegradationFit:
    return DegradationFit(
        session_year=KEY.year,
        session_round=KEY.round,
        session_type=KEY.session_type.value,
        driver=driver,
        stint_number=stint,
        compound=compound,
        deg_rate_s_per_lap=rate,
        deg_rate_ci_low_s_per_lap=ci_low if ci_low is not None else rate,
        deg_rate_ci_high_s_per_lap=ci_high if ci_high is not None else rate,
        intercept_s=intercept,
        quadratic_term_s_per_lap2=None,
        n_laps_used=n_laps,
        r2=1.0,
        reliability=reliability,
    )


def _lap_row(
    driver: str,
    lap_number: int,
    *,
    end_offset_s: float,
    tyre_age: int,
    stint: int = 1,
    compound: str = "medium",
    track_status: str = "green",
) -> dict[str, object]:
    end = RACE_START + timedelta(seconds=90.0 * lap_number + end_offset_s)
    return {
        "session_year": KEY.year,
        "session_round": KEY.round,
        "session_type": KEY.session_type.value,
        "driver": driver,
        "lap_number": lap_number,
        "lap_start_utc": end - timedelta(seconds=90.0),
        "lap_end_utc": end,
        "lap_time_s": 90.0,
        "sector1_s": None,
        "sector2_s": None,
        "sector3_s": None,
        "is_personal_best": False,
        "compound": compound,
        "tyre_age": tyre_age,
        "stint_number": stint,
        "position": None,
        "gap_ahead_s": None,
        "gap_ahead_min_s": None,
        "gap_ahead_samples": 0,
        "gap_to_leader_s": None,
        "track_status": track_status,
        "track_status_flags": [] if track_status == "green" else [track_status],
        "is_pit_in": False,
        "is_pit_out": False,
        "provenance": "historical",
    }


def _pair_state(
    decision_lap: int,
    *,
    gap_s: float,
    attacker: str = "44",
    defender: str = "1",
    attacker_age: int = 10,
    defender_age: int = 12,
    track_status: str = "green",
) -> SessionFrames:
    """A two-car frame with exactly one row each at the decision lap: the
    defender crosses the line first, the attacker `gap_s` later."""
    rows = [
        _lap_row(
            defender,
            decision_lap,
            end_offset_s=0.0,
            tyre_age=defender_age,
            track_status=track_status,
        ),
        _lap_row(
            attacker,
            decision_lap,
            end_offset_s=gap_s,
            tyre_age=attacker_age,
            track_status=track_status,
        ),
    ]
    laps = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA)
    return SessionFrames(
        session_key=KEY, laps=laps, stints=empty_frame(STINT_FRAME_SCHEMA), circuit=None
    )


# ---------------------------------------------------------------------------
# undercut: the hand-computed cases
# ---------------------------------------------------------------------------


def test_undercut_crossover_matches_the_hand_computed_case(params: KernelParams) -> None:
    """Equal intercepts (90.0) and rates (0.1), attacker age 10 vs defender
    age 12, gap 2.0s. By hand: margin(L) = -3.0 + 1.2*L, so the covered
    margin (L=2) is -0.6, the crossover lands at L=3 with +0.6, and the
    countdown scan gives margin(m) = -0.6 + 0.3*m -> opens in 3 laps."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.kind is CrossoverKind.UNDERCUT
    assert result.gap_s == pytest.approx(2.0)
    assert result.attacker_stop_lap == decision + 1
    assert result.crossover_lap == decision + 3
    assert result.projected_gap_at_crossover_s == pytest.approx(0.6)
    assert result.projected_gap_if_covered_s == pytest.approx(-0.6)
    assert result.window is WindowState.OPENS_LATER
    assert result.opens_in_laps == 3
    assert result.confidence is Confidence.HIGH


def test_undercut_open_now_when_the_gap_is_small(params: KernelParams) -> None:
    """Same paces, gap 0.5s: margin(2) = -1.5 + 2.4 = +0.9 — pitting now
    beats even an immediate cover."""
    decision = 20
    state = _pair_state(decision, gap_s=0.5)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.window is WindowState.OPEN
    assert result.opens_in_laps == 0
    assert result.crossover_lap == decision + 2
    assert result.projected_gap_if_covered_s == pytest.approx(0.9)
    assert result.projected_gap_at_crossover_s == pytest.approx(0.9)


def test_undercut_closed_when_the_attacker_degrades_faster(params: KernelParams) -> None:
    """Attacker rate 0.3 vs defender 0.05 (equal age 10): fresh attacker
    laps cannot make up the gap, and waiting only widens it — closed, no
    crossover, and the reasons say so."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0, attacker_age=10, defender_age=10)
    fits = [_fit("44", rate=0.3), _fit("1", rate=0.05)]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.window is WindowState.CLOSED
    assert result.crossover_lap is None
    assert result.opens_in_laps is None
    assert result.projected_gap_if_covered_s is not None
    assert result.projected_gap_if_covered_s < 0
    assert any("does not come through" in reason for reason in result.reasons)


def test_undercut_margin_at_an_assumed_defender_stop(params: KernelParams) -> None:
    """The what-if the backtest scores: margin(L) = -3.0 + 1.2*L evaluated
    at a concrete defender in-lap."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(
        state, "44", "1", fits, params, decision_lap=decision, assume_defender_stop_lap=decision + 5
    )

    assert result.projected_margin_at_assumed_stop_s == pytest.approx(-3.0 + 1.2 * 5)


def test_undercut_horizon_is_capped_by_the_race_end(params: KernelParams) -> None:
    """With the race ending 3 laps after the decision, the last coverable
    defender in-lap is total_laps - 1."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(
        state, "44", "1", fits, params, decision_lap=decision, total_laps=decision + 3
    )

    assert result.horizon_lap == decision + 2
    # crossover needs L=3 but the race only allows L=2 -> closed.
    assert result.window is WindowState.CLOSED
    assert result.crossover_lap is None


# ---------------------------------------------------------------------------
# overcut: the mirror
# ---------------------------------------------------------------------------


def test_overcut_deadline_matches_the_hand_computed_case(params: KernelParams) -> None:
    """Defender (age 25) pits now; attacker (age 4) stays out. Equal rates
    0.1, equal intercepts, gap 1.0: margin(L) = 2.5 - 0.4*L - 1.0, so
    staying out works at L=2 (+0.7) and L=3 (+0.3) but not L=4 (-0.1):
    the deadline is decision + 3."""
    decision = 30
    state = _pair_state(decision, gap_s=1.0, attacker_age=4, defender_age=25)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = overcut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.kind is CrossoverKind.OVERCUT
    assert result.defender_stop_lap == decision + 1
    assert result.attacker_stop_lap is None
    assert result.window is WindowState.OPEN
    assert result.crossover_lap == decision + 3
    assert result.projected_gap_at_crossover_s == pytest.approx(0.3)
    assert result.projected_gap_if_covered_s == pytest.approx(0.7)
    assert result.opens_in_laps is None


def test_overcut_closed_when_fresh_air_wins_immediately(params: KernelParams) -> None:
    """Defender barely worn (age 2): their fresh pace beats the attacker's
    old pace at once — no lap of staying out keeps the place."""
    decision = 30
    state = _pair_state(decision, gap_s=1.0, attacker_age=10, defender_age=2)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = overcut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.window is WindowState.CLOSED
    assert result.crossover_lap is None


# ---------------------------------------------------------------------------
# the invariants
# ---------------------------------------------------------------------------


def test_pit_loss_cancels_in_the_margin_but_moves_the_rejoin_gap(params: KernelParams) -> None:
    """The module docstring's promise, as an executable fact: equal pit
    losses drop out of every after-both-stops margin, and land 1:1 in the
    projected rejoin gap."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    cheap = undercut_window(state, "44", "1", fits, params, decision_lap=decision, pit_loss_s=15.0)
    dear = undercut_window(state, "44", "1", fits, params, decision_lap=decision, pit_loss_s=30.0)

    assert cheap.projected_gap_if_covered_s == pytest.approx(dear.projected_gap_if_covered_s)
    assert cheap.crossover_lap == dear.crossover_lap
    assert cheap.projected_rejoin_gap_s is not None and dear.projected_rejoin_gap_s is not None
    assert dear.projected_rejoin_gap_s - cheap.projected_rejoin_gap_s == pytest.approx(15.0)


def test_undercut_window_is_deterministic(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.12), _fit("1", rate=0.08)]

    first = undercut_window(state, "44", "1", fits, params, decision_lap=decision)
    second = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert first == second


def test_compound_change_shifts_the_attacker_fresh_pace(params: KernelParams) -> None:
    """Switching medium -> hard makes every fresh attacker lap slower by
    the compound-offset delta, so the covered margin drops by 2x it."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]
    delta = params.compound_offset("hard") - params.compound_offset("medium")

    same = undercut_window(state, "44", "1", fits, params, decision_lap=decision)
    harder = undercut_window(
        state, "44", "1", fits, params, decision_lap=decision, attacker_new_compound="hard"
    )

    assert same.projected_gap_if_covered_s is not None
    assert harder.projected_gap_if_covered_s == pytest.approx(
        same.projected_gap_if_covered_s - 2 * delta
    )


# ---------------------------------------------------------------------------
# honesty: UNKNOWN and the confidence ladder
# ---------------------------------------------------------------------------


def test_no_fit_yields_unknown_not_a_guess(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0)

    result = undercut_window(state, "44", "1", [], params, decision_lap=decision)

    assert result.window is WindowState.UNKNOWN
    assert result.confidence is Confidence.LOW
    assert result.crossover_lap is None
    assert result.projected_gap_if_covered_s is None
    assert any("no degradation fit" in reason for reason in result.reasons)


def test_missing_driver_row_yields_unknown(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision + 5)

    assert result.window is WindowState.UNKNOWN
    assert any("no completed lap" in reason for reason in result.reasons)


def test_a_thin_fit_caps_confidence_at_medium(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [
        _fit("44", rate=0.1, n_laps=3, reliability=Reliability.THIN),
        _fit("1", rate=0.1),
    ]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.window is not WindowState.UNKNOWN
    assert result.confidence is Confidence.MEDIUM
    assert any("attacker fit is thin" in reason for reason in result.reasons)


def test_an_unreliable_fit_caps_confidence_at_low(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [
        _fit("44", rate=0.1),
        _fit("1", rate=0.1, reliability=Reliability.UNRELIABLE),
    ]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.confidence is Confidence.LOW


def test_a_margin_inside_the_rate_cis_caps_confidence_at_medium(params: KernelParams) -> None:
    """Good fits, but rate CIs wide enough that the -0.6s covered margin
    flips sign at the extremes -> the call is honest about being soft."""
    decision = 20
    state = _pair_state(decision, gap_s=2.0)
    fits = [
        _fit("44", rate=0.1, ci_low=0.02, ci_high=0.18),
        _fit("1", rate=0.1, ci_low=0.02, ci_high=0.18),
    ]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.confidence is Confidence.MEDIUM
    assert result.projected_gap_if_covered_low_s is not None
    assert result.projected_gap_if_covered_high_s is not None
    assert result.projected_gap_if_covered_low_s < 0 < result.projected_gap_if_covered_high_s
    assert any("changes sign" in reason for reason in result.reasons)


def test_a_non_green_decision_lap_caps_confidence_at_low(params: KernelParams) -> None:
    decision = 20
    state = _pair_state(decision, gap_s=2.0, track_status="sc")
    fits = [_fit("44", rate=0.1), _fit("1", rate=0.1)]

    result = undercut_window(state, "44", "1", fits, params, decision_lap=decision)

    assert result.confidence is Confidence.LOW
    assert any("track not green" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# the per-lap snapshot primitive
# ---------------------------------------------------------------------------


def _running_order_frame(decision_lap: int, drivers_gaps: dict[str, float]) -> SessionFrames:
    """Several drivers with full lap histories 1..decision_lap, constant
    90s laps offset per driver, so the snapshot has real data to slice,
    clean, fuel-correct and fit."""
    rows: list[dict[str, object]] = []
    for driver, offset in drivers_gaps.items():
        for lap in range(1, decision_lap + 1):
            rows.append(_lap_row(driver, lap, end_offset_s=offset, tyre_age=lap, stint=1))
    laps = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA).sort(["driver", "lap_number"])
    return SessionFrames(
        session_key=KEY, laps=laps, stints=empty_frame(STINT_FRAME_SCHEMA), circuit=None
    )


def test_snapshot_pairs_adjacent_cars_in_race_order(params: KernelParams) -> None:
    state = _running_order_frame(12, {"1": 0.0, "44": 1.5, "16": 4.0})

    results = undercut_snapshot(state, params)

    assert [(r.attacker, r.defender) for r in results] == [("44", "1"), ("16", "44")]
    assert all(r.decision_lap == 12 for r in results)
    assert results[0].gap_s == pytest.approx(1.5)
    assert results[1].gap_s == pytest.approx(2.5)


def test_snapshot_skips_pairs_beyond_the_max_gap(params: KernelParams) -> None:
    wide = params.undercut_max_gap_s + 10.0
    state = _running_order_frame(12, {"1": 0.0, "44": wide})

    results = undercut_snapshot(state, params)

    assert results == []


def test_snapshot_results_flatten_to_a_frame(params: KernelParams) -> None:
    state = _running_order_frame(12, {"1": 0.0, "44": 1.5})

    table = crossover_results_to_frame(undercut_snapshot(state, params))

    assert table.height == 1
    assert table["kind"][0] == "undercut"
    assert table["attacker"][0] == "44"
    assert table["window"][0] in {state.value for state in WindowState}
