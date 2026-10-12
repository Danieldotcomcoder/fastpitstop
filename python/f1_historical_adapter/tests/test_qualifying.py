"""Qualifying rebuilt from history: parts from race control, the clock
stopped by a red flag, per-part best laps from the laps, and knockouts from
the results, checked on a hand-built session and, opt-in, against the
official 2024 Miami qualifying."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from f1_domain import (
    DomainEvent,
    QualifyingBestLap,
    QualifyingKnockout,
    QualifyingPartChanged,
    SessionClockUpdated,
    SessionKey,
    SessionType,
)
from f1_historical_adapter import events_from_parquet
from f1_historical_adapter.adapter import build_events
from f1_historical_adapter.qualifying import entries_for
from f1_historical_adapter.reader import SessionTables

KEY = SessionKey(year=2024, round=6, session_type=SessionType.QUALIFYING)
START = datetime(2024, 5, 4, 20, 0, tzinfo=UTC)
REAL_ROOT = Path(__file__).resolve().parents[3] / "data" / "historical"


def _at(minutes: float) -> str:
    return (START + timedelta(minutes=minutes)).isoformat()


def _rc(rows: list[tuple[float, str, str, int | None]]) -> pd.DataFrame:
    """(minutes after start, flag, message, qualifying_phase)."""
    return pd.DataFrame(
        {
            "date": [_at(m) for m, *_ in rows],
            "category": ["Flag"] * len(rows),
            "flag": [flag for _, flag, _, _ in rows],
            "message": [message for _, _, message, _ in rows],
            "qualifying_phase": [float(p) if p is not None else None for *_, p in rows],
        }
    )


RACE_CONTROL = _rc(
    [
        (0, "GREEN", "GREEN LIGHT - PIT EXIT OPEN", 1),
        (6, "RED", "RED FLAG", 1),
        (16, "GREEN", "GREEN LIGHT - PIT EXIT OPEN", 1),
        (28, "CHEQUERED", "CHEQUERED FLAG", 1),
        (35, "GREEN", "GREEN LIGHT - PIT EXIT OPEN", 2),
        (50, "CHEQUERED", "CHEQUERED FLAG", 2),
        (58, "GREEN", "GREEN LIGHT - PIT EXIT OPEN", 3),
        (70, "CHEQUERED", "CHEQUERED FLAG", 3),
    ]
)


def _lap(
    number: str, lap: int, began_min: float, seconds: float, deleted: bool = False
) -> dict[str, object]:
    began = timedelta(minutes=began_min)
    return {
        "DriverNumber": number,
        "LapNumber": float(lap),
        "LapStartTime": began,
        "Time": began + timedelta(seconds=seconds),
        "LapTime": timedelta(seconds=seconds),
        "Deleted": deleted,
    }


LAPS = pd.DataFrame(
    [
        _lap("1", 2, 3, 90.0),
        _lap("1", 4, 20, 89.5),
        _lap("1", 5, 23, 89.0, deleted=True),  # track limits: never shown
        _lap("1", 9, 40, 88.8),
        _lap("16", 2, 4, 89.9),
        _lap("16", 8, 41, 89.1),
        _lap("44", 2, 5, 91.2),
        # Started a second before the chequered flag: it counts.
        _lap("44", 3, 27.98, 90.4),
    ]
)

RESULTS = pd.DataFrame(
    {
        "DriverNumber": [str(n) for n in range(1, 23)],
        "Q1": [timedelta(seconds=88 + i / 10) for i in range(22)],
        "Q2": [timedelta(seconds=87 + i / 10) if i < 16 else pd.NaT for i in range(22)],
        "Q3": [timedelta(seconds=86 + i / 10) if i < 10 else pd.NaT for i in range(22)],
    }
)


def _events(laps: pd.DataFrame | None = LAPS, results: pd.DataFrame = RESULTS) -> list[DomainEvent]:
    tables: dict[str, pd.DataFrame] = {"race_control": RACE_CONTROL, "results": results}
    if laps is not None:
        tables["laps"] = laps
    return list(
        build_events(
            SessionTables(
                session_key=KEY, directory=Path("."), tables=tables, session_start_utc=START
            )
        )
    )


def test_cut_sizes_follow_the_field() -> None:
    assert entries_for(20) == [20, 15, 10]
    assert entries_for(22) == [22, 16, 10]


def test_each_part_opens_at_its_green_light() -> None:
    parts = [e for e in _events() if isinstance(e, QualifyingPartChanged)]
    assert [(p.part, p.ts_utc, p.entries) for p in parts] == [
        (1, START, [22, 16, 10]),
        (2, START + timedelta(minutes=35), [22, 16, 10]),
        (3, START + timedelta(minutes=58), [22, 16, 10]),
    ]


def test_a_red_flag_stops_the_clock() -> None:
    clock = [
        (round((e.ts_utc - START).total_seconds() / 60), e.remaining, e.running)
        for e in _events()
        if isinstance(e, SessionClockUpdated) and e.ts_utc < START + timedelta(minutes=30)
    ]
    # Q1 ran 6 + 12 = 18 minutes: 18 left at the start, 12 at the red flag,
    # still 12 when it restarts, none at the chequered flag.
    assert clock == [
        (0, timedelta(minutes=18), True),
        (6, timedelta(minutes=12), False),
        (16, timedelta(minutes=12), True),
        (28, timedelta(0), False),
    ]


def test_best_laps_improve_per_part_and_skip_deleted_ones() -> None:
    bests = [
        (e.driver, e.part, e.lap_number, e.lap_time)
        for e in _events()
        if isinstance(e, QualifyingBestLap)
    ]
    assert ("1", 1, 2, timedelta(seconds=90)) in bests
    assert ("1", 1, 4, timedelta(seconds=89.5)) in bests
    assert all(lap != 5 for driver, _, lap, _ in bests if driver == "1")
    assert ("1", 2, 9, timedelta(seconds=88.8)) in bests
    assert ("44", 1, 3, timedelta(seconds=90.4)) in bests


def test_the_cars_that_did_not_go_through_are_knocked_out_at_the_next_green() -> None:
    outs = [(e.part, e.driver, e.ts_utc) for e in _events() if isinstance(e, QualifyingKnockout)]
    assert [(p, d) for p, d, _ in outs] == [(1, str(n)) for n in range(17, 23)] + [
        (2, str(n)) for n in range(11, 17)
    ]
    assert {ts for p, _, ts in outs if p == 1} == {START + timedelta(minutes=35)}


def test_a_car_through_without_a_time_in_the_next_part_is_not_knocked_out() -> None:
    """Car 3 makes Q2 on its Q1 time but never runs in Q2."""
    results = RESULTS.copy()
    results.loc[results["DriverNumber"] == "3", "Q2"] = pd.NaT
    q1_out = [
        e.driver
        for e in _events(results=results)
        if isinstance(e, QualifyingKnockout) and e.part == 1
    ]
    assert "3" not in q1_out


def test_a_car_out_on_the_times_then_is_out_whatever_the_final_order() -> None:
    """2026 Singapore SQ1: Lindblad went out, yet ranks inside the cut on
    the final times, once laps deleted after the part are taken out. Car 5
    here: a top-five final Q1 time, no Q2 time, while car 17 went through."""
    results = RESULTS.copy()
    results.loc[results["DriverNumber"] == "5", "Q2"] = pd.NaT
    results.loc[results["DriverNumber"] == "17", "Q2"] = timedelta(seconds=89)
    q1_out = {
        e.driver
        for e in _events(results=results)
        if isinstance(e, QualifyingKnockout) and e.part == 1
    }
    assert q1_out == {"5", "18", "19", "20", "21", "22"}


@pytest.mark.skipif(
    not (REAL_ROOT / "season=2024" / "round=06" / "session=Qualifying").is_dir(),
    reason="2024 backfill not present",
)
def test_matches_the_official_2024_miami_qualifying() -> None:
    events = list(events_from_parquet(REAL_ROOT, KEY))
    best: dict[tuple[str, int], timedelta | None] = {}
    for event in events:
        if isinstance(event, QualifyingBestLap) and event.driver and event.part:
            best[(event.driver, event.part)] = event.lap_time
    results = pd.read_parquet(REAL_ROOT / "season=2024/round=06/session=Qualifying/results.parquet")
    for rec in results.to_dict("records"):
        for part in (1, 2, 3):
            official = rec[f"Q{part}"]
            expected = None if pd.isna(official) else official.to_pytimedelta()
            assert best.get((str(rec["DriverNumber"]), part)) == expected, (
                rec["Abbreviation"],
                part,
            )
    out = {(e.part, e.driver) for e in events if isinstance(e, QualifyingKnockout)}
    # Sargeant, Ricciardo, Magnussen, Zhou, Bottas; Gasly, Alonso, Stroll,
    # Albon, Ocon.
    assert out == {(1, n) for n in ("2", "3", "20", "24", "77")} | {
        (2, n) for n in ("10", "14", "18", "23", "31")
    }
