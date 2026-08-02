"""The walk-forward stop evaluator: a scripted two-car undercut is
discovered, predicted from pre-stop data only, and scored against the
outcome the lap times actually encode — plus the exclusion ledger, and a
smoke pass over the checked-in fixture races."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from kernel_fixture import ALL_RACES

from f1_domain import SessionKey, SessionType
from f1_kernel import (
    HistoricalSource,
    KernelParams,
    SessionFrames,
    evaluate_session_stops,
    evaluate_session_stops_rows,
    session_frames,
)
from f1_kernel.schemas import LAP_FRAME_SCHEMA, STINT_FRAME_SCHEMA, empty_frame

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
RACE_START = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)


def _scripted_undercut_race() -> SessionFrames:
    """Two cars, 18 laps. '44' runs 1.0s behind '1' for 11 laps, pits on
    lap 12, and emerges ahead once '1' pits on lap 15 — the lap times make
    the undercut succeed BY CONSTRUCTION (fresh 88.5s laps against the
    leader's degrading 90.5-91.5s ones), so the harness's ground truth is
    known exactly: attacker ahead by 8.5s at the outcome lap."""
    lap_times: dict[str, list[float]] = {
        # lap:        1     2..11    12     13      14     15     16     17    18
        "44": [91.0, *[90.0] * 10, 92.0, 105.0, 88.5, 88.5, 88.5, 88.5, 88.5],
        "1": [90.0, *[90.0] * 10, 90.5, 91.0, 91.5, 93.0, 106.0, 89.0, 89.0],
    }
    pit_in = {"44": 12, "1": 15}
    rows: list[dict[str, object]] = []
    for driver, times in lap_times.items():
        stop = pit_in[driver]
        clock = RACE_START
        for lap, lap_time in enumerate(times, start=1):
            end = clock + timedelta(seconds=lap_time)
            second_stint = lap > stop
            rows.append(
                {
                    "session_year": KEY.year,
                    "session_round": KEY.round,
                    "session_type": KEY.session_type.value,
                    "driver": driver,
                    "lap_number": lap,
                    "lap_start_utc": clock,
                    "lap_end_utc": end,
                    "lap_time_s": lap_time,
                    "sector1_s": None,
                    "sector2_s": None,
                    "sector3_s": None,
                    "is_personal_best": False,
                    "compound": "hard" if second_stint else "medium",
                    "tyre_age": lap - stop if second_stint else lap,
                    "stint_number": 2 if second_stint else 1,
                    "position": None,
                    "gap_ahead_s": None,
                    "gap_ahead_min_s": None,
                    "gap_ahead_samples": 0,
                    "gap_to_leader_s": None,
                    "track_status": "green",
                    "track_status_flags": [],
                    "is_pit_in": lap == stop,
                    "is_pit_out": lap == stop + 1,
                    "provenance": "historical",
                }
            )
            clock = end
    laps = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA).sort(["driver", "lap_number"])
    return SessionFrames(
        session_key=KEY, laps=laps, stints=empty_frame(STINT_FRAME_SCHEMA), circuit=None
    )


def test_a_scripted_undercut_is_discovered_predicted_and_scored(params: KernelParams) -> None:
    outcomes = evaluate_session_stops(_scripted_undercut_race(), params)

    undercuts = [o for o in outcomes if o.kind == "undercut"]
    assert len(undercuts) == 1
    duel = undercuts[0]
    assert (duel.attacker, duel.defender) == ("44", "1")
    assert duel.evaluated
    assert duel.decision_lap == 11
    assert duel.attacker_stop_lap == 12
    assert duel.defender_stop_lap == 15
    assert duel.truth_lap == 16
    assert duel.gap_at_decision_s == pytest.approx(1.0)
    assert duel.actual_ahead is True
    assert duel.actual_margin_s == pytest.approx(8.5)
    # Both cars ran identical clean pace before the stop, so the fitted
    # models are symmetric and the 1.0s gap falls to the attacker's fresh
    # tyres within two laps — the predictor must call this one correctly
    # from pre-stop data alone.
    assert duel.predicted_ahead is True
    assert duel.hit is True
    assert duel.predicted_margin_s is not None
    assert duel.confidence == "high"


def test_the_rival_who_never_stopped_again_lands_in_the_exclusion_ledger(
    params: KernelParams,
) -> None:
    """'1' stops on lap 15 with '44' directly behind — an overcut chance
    for '44'. But '44' (already stopped on 12) never stops again, so there
    is no after-both-stopped outcome to score, and the pair must be
    reported as excluded rather than silently skipped."""
    outcomes = evaluate_session_stops(_scripted_undercut_race(), params)

    overcuts = [o for o in outcomes if o.kind == "overcut"]
    assert len(overcuts) == 1
    assert overcuts[0].evaluated is False
    assert overcuts[0].exclusion == "rival_never_stopped_again"
    assert overcuts[0].hit is None


def test_evaluation_is_deterministic(params: KernelParams) -> None:
    frames = _scripted_undercut_race()

    assert evaluate_session_stops(frames, params) == evaluate_session_stops(frames, params)


def test_rows_adapter_matches_the_dataclasses(params: KernelParams) -> None:
    frames = _scripted_undercut_race()

    outcomes = evaluate_session_stops(frames, params)
    rows = evaluate_session_stops_rows(frames, params)

    assert len(rows) == len(outcomes)
    assert rows[0]["kind"] == outcomes[0].kind
    assert rows[0]["hit"] == outcomes[0].hit


def _real_race_available() -> bool:
    from pathlib import Path

    from f1_kernel.sources import list_historical_sessions

    root = Path(__file__).parents[3] / "data" / "historical"
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    return root.is_dir() and any(ref.session_key == key for ref in list_historical_sessions(root))


@pytest.mark.skipif(
    not _real_race_available(),
    reason="2024 Bahrain race not backfilled; run `uv run f1-backfill --season 2024`",
)
def test_a_real_race_is_predicted_better_than_a_coin_flip(params: KernelParams) -> None:
    """The milestone assertion, on one complete real race: walk-forward
    predictions over 2024 Bahrain's stop-pairs must beat chance by a
    margin, with a healthy number of scored duels. (The committed
    2024/2025 corpus reports come from `tools/backtest_undercut.py`; this
    pins one race so a regression cannot land silently. Observed at pin
    time: 53 evaluated, 35 scored, 83% hit.)"""
    from pathlib import Path

    real_source = HistoricalSource(Path(__file__).parents[3] / "data" / "historical")
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)

    outcomes = evaluate_session_stops(session_frames(real_source, key), params)

    evaluated = [o for o in outcomes if o.evaluated]
    scored = [o for o in evaluated if o.hit is not None]
    assert len(evaluated) >= 40
    assert len(scored) >= 25
    hit_rate = sum(1 for o in scored if o.hit) / len(scored)
    assert hit_rate > 0.6, f"walk-forward hit rate degraded to {hit_rate:.1%}"
    # The confidence machinery must actually spread — a predictor that
    # calls everything MEDIUM has stopped saying anything.
    assert {o.confidence for o in scored} >= {"high", "medium"}


def test_the_fixture_races_produce_stop_pairs(
    source: HistoricalSource, params: KernelParams
) -> None:
    """The checked-in synthetic races contain staggered green(ish) stops
    for eight cars; the evaluator must find duels there end-to-end through
    the real frame-building path — the same shape the 2024/2025 backtest
    runs at scale."""
    all_outcomes = [
        outcome
        for key in ALL_RACES
        for outcome in evaluate_session_stops(session_frames(source, key), params)
    ]

    assert all_outcomes, "no stop-pairs found in the fixture races at all"
    evaluated = [o for o in all_outcomes if o.evaluated]
    assert evaluated, "every fixture stop-pair was excluded"
    for outcome in evaluated:
        assert outcome.actual_ahead is not None
        assert outcome.truth_lap is not None
        assert outcome.window is not None
        if outcome.predicted_ahead is not None:
            assert outcome.hit == (outcome.predicted_ahead == outcome.actual_ahead)
