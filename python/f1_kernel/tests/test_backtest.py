"""The backtest harness — how every later analytic gets tested, so it had
better work over more than one session."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from kernel_fixture import ALL_RACES, GREEN_RACE, HISTORICAL_ROOT, SC_RACE

from f1_domain import SessionKey, SessionType
from f1_kernel import (
    Backtest,
    BacktestError,
    HistoricalSource,
    KernelParams,
    SessionFrames,
    list_historical_sessions,
)
from f1_kernel.frames import SessionFrames as Frames


def lap_count_analytic(frames: SessionFrames, params: KernelParams) -> dict[str, object]:
    """A trivial identity-ish analytic: count what came in. Deliberately
    dull — this test is about the harness, not about analysis."""
    return {
        "drivers": len(frames.drivers),
        "laps": frames.laps.height,
        "dirty_air_threshold_s": params.dirty_air_threshold_s,
    }


def per_driver_analytic(frames: SessionFrames, params: KernelParams) -> pl.DataFrame:
    """An analytic returning many rows, as most real ones will."""
    return frames.laps.group_by("driver").agg(pl.len().alias("laps")).sort("driver")


def test_lists_available_historical_sessions() -> None:
    """The "what can I backtest over?" entry point."""
    sessions = list_historical_sessions(HISTORICAL_ROOT)

    assert [ref.session_key for ref in sessions] == list(ALL_RACES)
    assert sessions[0].event_name == "Bahrain Grand Prix"
    assert "laps" in sessions[0].tables


def test_listing_a_missing_root_is_empty_not_an_error(tmp_path: Path) -> None:
    """A machine with no backfill yet is a normal state."""
    assert list_historical_sessions(tmp_path) == []
    assert list_historical_sessions(tmp_path / "nope") == []


def test_sessions_can_be_filtered() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    assert len(backtest.sessions()) == 2
    assert len(backtest.sessions(session_types=[SessionType.RACE])) == 2
    assert backtest.sessions(session_types=[SessionType.QUALIFYING]) == []
    assert len(backtest.sessions(seasons=[2024], limit=1)) == 1
    assert backtest.sessions(seasons=[2023]) == []


def test_runs_an_analytic_over_multiple_sessions_and_tabulates() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(lap_count_analytic)

    assert result.sessions_run == 2
    assert result.sessions_failed == 0
    assert result.table.height == 2
    # Every row is stamped with the session it came from.
    assert result.table["session_round"].to_list() == [1, 2]
    assert result.table["event_name"].to_list() == [
        "Bahrain Grand Prix",
        "Saudi Arabian Grand Prix",
    ]
    assert result.table["drivers"].to_list() == [8, 8]
    assert result.table["laps"].to_list() == [240, 208]
    assert "backtest: 2 session(s) -> 2 row(s)" in result.summary()


def test_multi_row_analytics_are_stacked() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(per_driver_analytic)

    assert result.table.height == 16  # 8 drivers x 2 sessions
    assert set(result.table["session_round"].to_list()) == {1, 2}
    assert result.table.filter(pl.col("session_round") == 1)["laps"].sum() == 240


def test_the_params_used_are_recorded_with_the_result() -> None:
    """A result without its parameters is not a result."""
    params = KernelParams().with_overrides(dirty_air_threshold_s=0.7)
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT), params=params)

    result = backtest.run(lap_count_analytic)

    assert result.params == params
    assert result.table["dirty_air_threshold_s"].to_list() == [0.7, 0.7]


def test_params_can_be_overridden_per_run() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(
        lap_count_analytic, params=KernelParams().with_overrides(dirty_air_threshold_s=1.9)
    )

    assert result.table["dirty_air_threshold_s"].to_list() == [1.9, 1.9]


def test_compare_runs_several_analytics_into_one_table() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.compare({"counts": lap_count_analytic, "per_driver": per_driver_analytic})

    assert set(result.table["analytic"].to_list()) == {"counts", "per_driver"}
    counts = result.table.filter(pl.col("analytic") == "counts")
    assert counts.height == 2
    assert result.table.filter(pl.col("analytic") == "per_driver").height == 16


def test_one_failing_session_does_not_end_the_run() -> None:
    """Over ~80 races something will be malformed. The run reports it and
    keeps going."""
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    def flaky(frames: Frames, params: KernelParams) -> dict[str, object]:
        if frames.session_key.round == 1:
            raise ValueError("boom")
        return {"ok": True}

    result = backtest.run(flaky)

    assert result.sessions_run == 1
    assert result.sessions_failed == 1
    assert result.table.height == 1
    assert "boom" in str(result.errors[0])
    assert "2024 R01 race" in str(result.errors[0])
    with pytest.raises(BacktestError):
        result.raise_for_errors()


def test_raise_on_error_surfaces_the_original_exception() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    def broken(frames: Frames, params: KernelParams) -> dict[str, object]:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        backtest.run(broken, raise_on_error=True)


def test_a_missing_session_is_captured_as_an_error() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))
    missing = SessionKey(year=2024, round=99, session_type=SessionType.RACE)

    result = backtest.run(lap_count_analytic, [missing])

    assert result.sessions_run == 0
    assert result.sessions_failed == 1


def test_analytic_returning_nothing_contributes_no_rows() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(lambda frames, params: None)

    assert result.sessions_run == 2
    assert result.table.is_empty()


def test_backtest_can_run_on_a_chosen_subset() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(lap_count_analytic, [SC_RACE])

    assert result.sessions_run == 1
    assert result.table["session_round"].to_list() == [1]


def test_loading_a_session_gives_frames_with_circuit_metadata() -> None:
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    frames = backtest.load(backtest.sessions()[0])

    assert frames.session_key == SC_RACE
    assert frames.circuit is not None
    assert not frames.laps.is_empty()
    assert not frames.stints.is_empty()


def test_analytic_columns_are_not_clobbered_by_session_columns() -> None:
    """An analytic that computes its own `session_round` keeps it."""
    backtest = Backtest(HistoricalSource(HISTORICAL_ROOT))

    result = backtest.run(lambda frames, params: {"session_round": -1}, [GREEN_RACE])

    assert result.table["session_round"].to_list() == [-1]
