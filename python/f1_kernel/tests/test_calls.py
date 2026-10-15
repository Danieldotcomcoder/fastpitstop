"""The calls ledger and scorecard (ROADMAP W5).

The property tests replay each checked-in fixture race as a viewer would
receive it: the ledger is recomputed over the laps completed by each
moment, just before and at every instant a call locks or resolves, and
every few laps besides. At every one of those moments:
- no call, and no resolution, is dated after it (nothing from the future);
- a call that has resolved never changes its status or its confidence;
- at the flag, the undercut calls ARE the backtest's undercut rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest
from kernel_fixture import ALL_RACES
from race_script import released_by, scripted_race

from f1_domain import SessionKey
from f1_kernel import (
    Call,
    CallKind,
    CallStatus,
    HistoricalSource,
    KernelParams,
    PitLossEstimate,
    SessionFrames,
    call_ledger,
    evaluate_session_stops,
    scorecard,
    session_frames,
)
from f1_kernel.undercut import Confidence

TICK = timedelta(microseconds=1)


def _moments(frames: SessionFrames, final_calls: list[Call]) -> list[datetime]:
    moments: set[datetime] = set()
    for call in final_calls:
        for moment in (call.locked_at_utc, call.resolved_at_utc):
            if moment is not None:
                moments |= {moment - TICK, moment}
    leader_ends = (
        frames.laps.group_by("lap_number")
        .agg(pl.col("lap_end_utc").min())
        .sort("lap_number")["lap_end_utc"]
        .to_list()
    )
    moments |= set(leader_ends[::4])
    return sorted(moments)


def _assert_never_ahead_and_never_changes(
    frames: SessionFrames, params: KernelParams, pit_loss: PitLossEstimate | None = None
) -> None:
    total = frames.laps["lap_number"].max()
    assert isinstance(total, int)
    final = {c.call_id: c for c in call_ledger(frames, params, total_laps=total, pit_loss=pit_loss)}
    assert final, "the race locks no calls at all"

    settled: dict[str, Call] = {}
    for moment in _moments(frames, list(final.values())):
        for call in call_ledger(
            released_by(frames, moment), params, total_laps=total, final=False, pit_loss=pit_loss
        ):
            assert call.locked_at_utc <= moment, call
            assert call.resolved_at_utc is None or call.resolved_at_utc <= moment, call
            assert (call.status is CallStatus.PENDING) == (call.resolved_at_utc is None), call
            if call.call_id in settled:
                before = settled[call.call_id]
                assert (call.status, call.confidence) == (before.status, before.confidence)
            if call.status is not CallStatus.PENDING:
                settled[call.call_id] = call
            # Its claim never moves either, pending or not.
            assert call.confidence is final[call.call_id].confidence
            assert call.crossover_lap == final[call.call_id].crossover_lap
            assert call.projected_position == final[call.call_id].projected_position

    for call_id, call in settled.items():
        assert final[call_id].status is call.status


@pytest.mark.parametrize("key", ALL_RACES, ids=lambda key: f"round{key.round}")
def test_the_ledger_never_runs_ahead_of_the_clock_and_never_changes_its_mind(
    source: HistoricalSource, params: KernelParams, key: SessionKey
) -> None:
    _assert_never_ahead_and_never_changes(session_frames(source, key), params)


_GRID = {"A": 0.0, "B": 3.0, "X": 6.0, "C": 12.0, "D": 30.0}


@pytest.mark.parametrize(
    "stops",
    [
        # D, just behind X's rejoin point, pits at the end of X's out-lap:
        # it crosses after X, and must void X's call, never flip it.
        {"X": (10, 20.0), "D": (11, 20.0)},
        # C, the car X rejoins behind, pits on X's in-lap.
        {"X": (10, 20.0), "C": (10, 20.0)},
        # Two stops a lap apart, each settled with the other around.
        {"X": (10, 20.0), "B": (12, 22.0), "D": (8, 21.0)},
        # A stop that costs more than projected: wrong, not void.
        {"X": (10, 26.0)},
    ],
    ids=["neighbour-pits-on-out-lap", "car-ahead-pits-same-lap", "staggered", "slow-stop"],
)
def test_scripted_edge_cases_never_run_ahead_or_change_their_mind(
    params: KernelParams, stops: dict[str, tuple[int, float]]
) -> None:
    race = scripted_race(offsets=_GRID, laps=16, stops=stops)
    _assert_never_ahead_and_never_changes(race, params, PitLossEstimate(20.0, 2.0, "calibrated"))


@pytest.mark.parametrize("key", ALL_RACES, ids=lambda key: f"round{key.round}")
def test_at_the_flag_the_undercut_calls_are_the_backtest(
    source: HistoricalSource, params: KernelParams, key: SessionKey
) -> None:
    frames = session_frames(source, key)
    ledger = [c for c in call_ledger(frames, params) if c.kind is CallKind.UNDERCUT]
    rows = [o for o in evaluate_session_stops(frames, params) if o.kind == "undercut"]

    expected = {
        (o.attacker, o.defender, o.attacker_stop_lap): (
            CallStatus.VOID
            if not o.evaluated
            else CallStatus.NO_CALL
            if o.hit is None
            else CallStatus.RIGHT
            if o.hit
            else CallStatus.WRONG,
            o.confidence,
        )
        for o in rows
    }
    got = {(c.driver, c.rival, c.in_lap): (c.status, c.confidence.value) for c in ledger}
    assert got == expected


def test_the_scorecard_counts_only_right_and_wrong_by_confidence(
    source: HistoricalSource, params: KernelParams
) -> None:
    calls = [c for key in ALL_RACES for c in call_ledger(session_frames(source, key), params)]

    card = scorecard(calls)

    assert [line.confidence for line in card.by_confidence] == [
        Confidence.HIGH,
        Confidence.MEDIUM,
        Confidence.LOW,
    ]
    assert card.right == sum(1 for c in calls if c.status is CallStatus.RIGHT)
    assert card.wrong == sum(1 for c in calls if c.status is CallStatus.WRONG)
    assert card.right + card.wrong + card.pending + card.void + card.no_call == len(calls)
    for line in card.by_confidence:
        assert line.right == sum(
            1 for c in calls if c.confidence is line.confidence and c.status is CallStatus.RIGHT
        )


def test_a_rejoin_call_locks_when_the_car_pits_and_resolves_with_its_neighbours(
    params: KernelParams,
) -> None:
    """The scripted race of `test_rejoin`: X pits at the end of lap 10."""
    race = scripted_race(
        offsets={"A": 0.0, "B": 3.0, "X": 6.0, "C": 12.0, "D": 30.0},
        laps=14,
        stops={"X": (10, 20.0)},
    )
    estimate = PitLossEstimate(20.0, 2.0, "calibrated")

    [call] = call_ledger(race, params, pit_loss=estimate, kinds=(CallKind.REJOIN,))

    x_in = race.laps.filter((pl.col("driver") == "X") & (pl.col("lap_number") == 10))
    d_out = race.laps.filter((pl.col("driver") == "D") & (pl.col("lap_number") == 11))
    assert call.call_id == "rejoin:X:10"
    assert call.locked_at_utc == x_in["lap_end_utc"][0]
    assert call.resolved_at_utc == d_out["lap_end_utc"][0]
    assert (call.rival, call.projected_position, call.confidence) == ("C", 4, Confidence.HIGH)
    assert (call.status, call.actual_car_ahead, call.actual_position) == (
        CallStatus.RIGHT,
        "C",
        4,
    )


def test_the_ledger_is_deterministic(source: HistoricalSource, params: KernelParams) -> None:
    frames = session_frames(source, ALL_RACES[0])

    assert call_ledger(frames, params) == call_ledger(frames, params)


def test_the_rejoin_backtest_report_renders_its_ordering_check(
    source: HistoricalSource, params: KernelParams
) -> None:
    """`tools/backtest_rejoin.py` over the fixture races (pit loss measured
    on the same races here: this checks the tool, not the model)."""
    import importlib.util
    from pathlib import Path

    from f1_kernel.pit_loss import calibrate_pit_loss

    path = Path(__file__).parents[1] / "tools" / "backtest_rejoin.py"
    spec = importlib.util.spec_from_file_location("backtest_rejoin", path)
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    calibration = calibrate_pit_loss(source, list(ALL_RACES), params, min_stops_per_circuit=1)
    estimates = tool.estimates_by_circuit(calibration.circuits)
    analytic = tool.rows_with(estimates)
    table = pl.DataFrame(
        [row for key in ALL_RACES for row in analytic(session_frames(source, key), params)]
    )
    report = tool.render_report(["fixture"], [], table, [2024], estimates)

    assert estimates, "no circuit measured"
    assert all(e.sigma_s is not None for e in estimates.values())
    assert "## W5 ordering check" in report
    assert "## Coverage" in report
    assert f"- Green-flag stops called: **{table.height}**" in report


def _w5_verdict(report: str, heading: str | None) -> bool:
    """Whether the committed report's W5 ordering check passes: the first
    verdict line in the whole report (`heading=None`), or in the section
    under `heading`, up to the next heading."""
    if heading is not None:
        if heading not in report:
            return False
        section = report.split(heading, 1)[1].splitlines()[1:]
        cut = next((i for i, line in enumerate(section) if line.startswith("#")), len(section))
        lines = section[:cut]
    else:
        lines = report.splitlines()
    verdict = next((line for line in lines if line.startswith("**W5 ")), "")
    return verdict.startswith("**W5 PASS**")


def test_the_cleared_call_kinds_match_the_committed_held_out_reports() -> None:
    """A call kind reaches the live desk only with a passing 2025 report."""
    from pathlib import Path

    from f1_kernel import CLEARED_CALL_KINDS

    reports = Path(__file__).parents[1] / "reports"
    undercut = reports / "undercut_backtest.md"
    rejoin = reports / "rejoin_backtest.md"
    passing: set[CallKind] = set()
    if undercut.is_file() and _w5_verdict(
        undercut.read_text(encoding="utf-8"), "### Undercut calls only"
    ):
        passing.add(CallKind.UNDERCUT)
    if rejoin.is_file() and _w5_verdict(rejoin.read_text(encoding="utf-8"), None):
        passing.add(CallKind.REJOIN)

    assert set(CLEARED_CALL_KINDS) == passing


def test_the_verdict_reader_finds_a_pass_only_in_its_own_section() -> None:
    report = "\n".join(
        [
            "## W5 ordering check",
            "**W5 PASS**: all calls",
            "### Undercut calls only",
            "",
            "table",
            "**W5 FAIL**: high=1 > medium=2 > low=3",
            "### Overcut calls only",
            "**W5 PASS**: overcut",
        ]
    )

    assert _w5_verdict(report, None) is True
    assert _w5_verdict(report, "### Undercut calls only") is False
    assert _w5_verdict(report, "### Overcut calls only") is True
    assert _w5_verdict(report, "### Missing") is False
