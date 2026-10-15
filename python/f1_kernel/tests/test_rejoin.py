"""Rejoin calls (ROADMAP W5): the projection from line crossings plus a
measured pit loss, its confidence against one stop's spread, and the
outcome scored at the end of the out-lap — on a scripted race whose
answer is worked out by hand in each test."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from race_script import START, released_by, scripted_race

from f1_kernel import (
    KernelParams,
    PitLossEstimate,
    RejoinOutcome,
    evaluate_session_rejoins,
    load_circuit,
    pit_loss_estimate,
    project_rejoin,
    rejoin_snapshot,
)
from f1_kernel.pit_loss import MEDIAN_SE_FACTOR
from f1_kernel.undercut import Confidence

# Crossing the line at the end of lap 9, seconds after A: A 0, B 3, X 6,
# C 12, D 30. X pits at the end of lap 10 and loses 20 s.
OFFSETS = {"A": 0.0, "B": 3.0, "X": 6.0, "C": 12.0, "D": 30.0}
MEASURED = PitLossEstimate(value_s=20.0, sigma_s=2.0, provenance="calibrated")


def test_the_projection_reads_who_is_ahead_after_one_pit_loss(params: KernelParams) -> None:
    """X is 6 s behind A at lap 9. Losing 20 s puts it 14 s behind C (now
    6 s behind it) and 4 s ahead of D (now 24 s behind): P4, behind C."""
    race = scripted_race(offsets=OFFSETS, laps=14)

    projection = project_rejoin(race, "X", 9, params, pit_loss=MEASURED)

    assert projection is not None
    assert projection.in_lap == 10
    assert projection.position_now == 3
    assert projection.position == 4
    assert projection.car_ahead == "C"
    assert projection.gap_to_ahead_s == pytest.approx(14.0)
    assert projection.car_behind == "D"
    assert projection.gap_to_behind_s == pytest.approx(4.0)
    assert projection.margin_s == pytest.approx(4.0)


@pytest.mark.parametrize(
    ("sigma", "expected"),
    [
        (2.0, Confidence.HIGH),
        (3.0, Confidence.MEDIUM),
        (5.0, Confidence.LOW),
        (None, Confidence.LOW),
    ],
)
def test_confidence_is_the_clear_air_against_one_stops_spread(
    params: KernelParams, sigma: float | None, expected: Confidence
) -> None:
    """4 s of clear air is 2 spreads of 2 s (high), 1.3 of 3 s (medium),
    0.8 of 5 s (low); with no measured spread there is nothing to judge
    by, so low."""
    race = scripted_race(offsets=OFFSETS, laps=14)
    estimate = PitLossEstimate(20.0, sigma, "calibrated" if sigma else "provisional")

    projection = project_rejoin(race, "X", 9, params, pit_loss=estimate)

    assert projection is not None
    assert projection.confidence is expected


def test_a_decision_lap_under_the_safety_car_is_low_confidence(params: KernelParams) -> None:
    race = scripted_race(offsets=OFFSETS, laps=14, neutral_laps=frozenset({9}))

    projection = project_rejoin(race, "X", 9, params, pit_loss=MEASURED)

    assert projection is not None
    assert projection.confidence is Confidence.LOW
    assert any("not green" in reason for reason in projection.reasons)


def test_the_snapshot_projects_every_car_in_race_order(params: KernelParams) -> None:
    race = scripted_race(offsets=OFFSETS, laps=14)

    snapshot = rejoin_snapshot(race, params, decision_lap=9, pit_loss=MEASURED)

    assert [p.driver for p in snapshot] == ["A", "B", "X", "C", "D"]
    assert [p.position_now for p in snapshot] == [1, 2, 3, 4, 5]


def test_the_spread_is_recovered_exactly_from_a_circuit_file(params: KernelParams) -> None:
    """The file keeps the median's standard error, 1.2533 * sigma / sqrt(n)."""
    circuit = load_circuit("bahrain")
    race = scripted_race(offsets=OFFSETS, laps=14)
    race = type(race)(
        session_key=race.session_key, laps=race.laps, stints=race.stints, circuit=circuit
    )

    estimate = pit_loss_estimate(race, params)

    assert circuit.pit_loss_uncertainty_s is not None and circuit.pit_loss_n_stops
    assert estimate.value_s == circuit.pit_loss_s
    assert estimate.provenance == "calibrated"
    assert estimate.sigma_s == pytest.approx(
        circuit.pit_loss_uncertainty_s * math.sqrt(circuit.pit_loss_n_stops) / MEDIAN_SE_FACTOR
    )


def test_a_rejoin_that_comes_out_where_projected_is_right(params: KernelParams) -> None:
    race = scripted_race(offsets=OFFSETS, laps=14, stops={"X": (10, 20.0)})

    [outcome] = evaluate_session_rejoins(race, params, pit_loss=MEASURED)

    assert outcome.evaluated
    assert outcome.hit is True
    assert outcome.actual_car_ahead == "C"
    assert outcome.actual_position == 4
    # Settled when D, the car just behind, finished the out-lap too.
    assert outcome.settled_at_utc == START + timedelta(seconds=30 + 11 * 90)


def test_a_stop_that_costs_more_than_projected_is_wrong(params: KernelParams) -> None:
    """26 s instead of 20: X crosses 2 s after D."""
    race = scripted_race(offsets=OFFSETS, laps=14, stops={"X": (10, 26.0)})

    [outcome] = evaluate_session_rejoins(race, params, pit_loss=MEASURED)

    assert outcome.hit is False
    assert outcome.actual_car_ahead == "D"
    assert outcome.actual_position == 5


def test_a_neighbour_pitting_on_the_same_laps_voids_the_call(params: KernelParams) -> None:
    race = scripted_race(offsets=OFFSETS, laps=14, stops={"X": (10, 20.0), "D": (11, 20.0)})

    x = next(
        o for o in evaluate_session_rejoins(race, params, pit_loss=MEASURED) if o.driver == "X"
    )

    assert not x.evaluated
    assert x.exclusion == "another_car_pitted_around_the_rejoin"
    assert x.hit is None


def test_a_safety_car_on_the_out_lap_voids_the_call(params: KernelParams) -> None:
    race = scripted_race(
        offsets=OFFSETS, laps=14, stops={"X": (10, 20.0)}, neutral_laps=frozenset({11})
    )

    [outcome] = evaluate_session_rejoins(race, params, pit_loss=MEASURED)

    assert outcome.exclusion == "stop_not_green"
    assert outcome.hit is None


def test_a_running_race_waits_for_the_out_lap_and_the_cars_around(params: KernelParams) -> None:
    """Live, the call locks when X pits, waits through its out-lap, then
    for D (which could still pit and void it), then resolves."""
    race = scripted_race(offsets=OFFSETS, laps=14, stops={"X": (10, 20.0)})
    x_out = START + timedelta(seconds=6 + 11 * 90 + 20)
    d_out = START + timedelta(seconds=30 + 11 * 90)

    def at(moment: datetime) -> list[RejoinOutcome]:
        return evaluate_session_rejoins(
            released_by(race, moment), params, pit_loss=MEASURED, final=False
        )

    [before_out] = at(x_out - timedelta(microseconds=1))
    [before_d] = at(x_out)
    [settled] = at(d_out)

    assert before_out.pending and before_out.exclusion == "waiting_for_out_lap"
    assert before_d.pending and before_d.exclusion == "waiting_for_cars_around"
    assert not settled.pending and settled.hit is True
    assert settled.projection == before_out.projection


def test_the_snapshot_skips_a_car_in_the_pits_or_just_out(params: KernelParams) -> None:
    """At lap 10 X is in the pits (in-lap); at lap 11 it is on its out-lap.
    Neither gets a "box next lap" projection, though it still counts as a
    car on track for the others."""
    race = scripted_race(offsets=OFFSETS, laps=14, stops={"X": (10, 20.0)})

    at_in = rejoin_snapshot(race, params, decision_lap=10, pit_loss=MEASURED)
    at_out = rejoin_snapshot(race, params, decision_lap=11, pit_loss=MEASURED)
    after = rejoin_snapshot(race, params, decision_lap=12, pit_loss=MEASURED)

    assert "X" not in [p.driver for p in at_in]
    assert "X" not in [p.driver for p in at_out]
    assert "X" in [p.driver for p in after]
    assert len(at_in) == 4
