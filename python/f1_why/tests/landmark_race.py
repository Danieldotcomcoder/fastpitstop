"""A seven-car, twelve-lap synthetic race with every race landmark planted
and one trap, built directly as frames like `race_fixture.py`:

- lap 1: car "2" takes the lead from pole-sitter "1" (grid 1..7 in number
  order);
- lap 3: "4" passes "3" for P3 on track — a real overtake;
- laps 5-6: "5" pits (in-lap 5, out-lap 6) and drops behind "6" — the trap:
  "6" moving up is the pit lane, never an overtake;
- "7" stops after lap 4 running last (nobody behind it);
- "3" stops after lap 8, and a safety car runs laps 9-10 for it; leader
  "2" pits under it and keeps the lead;
- lap 12 is the flag: "2" wins from "1" and "4".

Positions are planted per lap (the frame's classification column); lap end
times follow from them, one second apart, so gap trends and anchors are
consistent with the order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from f1_domain import (
    DomainEvent,
    DriverListUpdated,
    DriverRef,
    PositionChanged,
    Provenance,
    SessionKey,
    SessionType,
)
from f1_kernel import STINT_FRAME_SCHEMA, SessionFrames
from f1_kernel.schemas import LAP_FRAME_SCHEMA

SESSION_KEY = SessionKey(year=2031, round=3, session_type=SessionType.RACE)
TOTAL_LAPS = 12
START = datetime(2031, 3, 9, 15, 0, 0, tzinfo=UTC)
LAP_S = 90.0
PIT_LOSS_S = 23.03
GRID = {str(n): n for n in range(1, 8)}

ORDER: dict[int, list[str]] = {
    1: ["2", "1", "3", "4", "5", "6", "7"],
    2: ["2", "1", "3", "4", "5", "6", "7"],
    3: ["2", "1", "4", "3", "5", "6", "7"],
    4: ["2", "1", "4", "3", "5", "6", "7"],
    5: ["2", "1", "4", "3", "5", "6"],
    6: ["2", "1", "4", "3", "6", "5"],
    7: ["2", "1", "4", "3", "6", "5"],
    8: ["2", "1", "4", "3", "6", "5"],
    9: ["2", "1", "4", "6", "5"],
    10: ["2", "1", "4", "6", "5"],
    11: ["2", "1", "4", "6", "5"],
    12: ["2", "1", "4", "6", "5"],
}
PIT_IN = {("5", 5), ("2", 9)}
PIT_OUT = {("5", 6), ("2", 10)}
SC_LAPS = {9, 10}

NAMES = {
    "1": "Max VERSTAPPEN",
    "2": "Lando NORRIS",
    "3": "Charles LECLERC",
    "4": "Oscar PIASTRI",
    "5": "Nyck De Vries",
    "6": "Guanyu ZHOU",
    "7": "Andrea Kimi ANTONELLI",
}


def _lap_end(lap_number: int, position: int) -> datetime:
    return START + timedelta(seconds=lap_number * LAP_S + (position - 1) * 1.0)


def build_frames() -> SessionFrames:
    rows: list[dict[str, object]] = []
    for lap_number, order in ORDER.items():
        for index, driver in enumerate(order):
            position = index + 1
            stop = 1 if (driver, lap_number) in PIT_OUT else 0
            in_stint_two = (driver == "5" and lap_number >= 6) or (
                driver == "2" and lap_number >= 10
            )
            first_lap = 6 if driver == "5" else 10
            status = "sc" if lap_number in SC_LAPS else "green"
            rows.append(
                {
                    "session_year": SESSION_KEY.year,
                    "session_round": SESSION_KEY.round,
                    "session_type": SESSION_KEY.session_type.value,
                    "driver": driver,
                    "lap_number": lap_number,
                    "lap_start_utc": _lap_end(lap_number - 1, position),
                    "lap_end_utc": _lap_end(lap_number, position),
                    "lap_time_s": LAP_S + stop * PIT_LOSS_S,
                    "sector1_s": 30.0,
                    "sector2_s": 30.0 + stop * PIT_LOSS_S,
                    "sector3_s": 30.0,
                    "is_personal_best": False,
                    "compound": "hard" if in_stint_two else "medium",
                    "tyre_age": lap_number - first_lap + 1 if in_stint_two else lap_number,
                    "stint_number": 2 if in_stint_two else 1,
                    "position": position,
                    "gap_ahead_s": None if position == 1 else 1.0,
                    "gap_ahead_min_s": None,
                    "gap_ahead_samples": 0,
                    "gap_to_leader_s": None,
                    "track_status": status,
                    "track_status_flags": [status],
                    "is_pit_in": (driver, lap_number) in PIT_IN,
                    "is_pit_out": (driver, lap_number) in PIT_OUT,
                    "provenance": "historical",
                }
            )
    laps = pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA).sort("driver", "lap_number")

    stint_rows: list[dict[str, object]] = []
    for driver in GRID:
        last = max(lap for lap, order in ORDER.items() if driver in order)
        stints = [(1, last, "medium")]
        if driver == "5":
            stints = [(1, 5, "medium"), (6, last, "hard")]
        if driver == "2":
            stints = [(1, 9, "medium"), (10, last, "hard")]
        for number, (start, end, compound) in enumerate(stints, start=1):
            stint_rows.append(
                {
                    "session_year": SESSION_KEY.year,
                    "session_round": SESSION_KEY.round,
                    "session_type": SESSION_KEY.session_type.value,
                    "driver": driver,
                    "stint_number": number,
                    "compound": compound,
                    "start_lap": start,
                    "end_lap": end,
                    "lap_count": end - start + 1,
                    "tyre_age_at_start": 0,
                    "provenance": "historical",
                }
            )
    stints = pl.DataFrame(stint_rows, schema=STINT_FRAME_SCHEMA).sort("driver", "stint_number")
    return SessionFrames(session_key=SESSION_KEY, laps=laps, stints=stints, event_name=None)


def _envelope(ts: datetime, seq: int) -> dict[str, object]:
    return {
        "session_key": SESSION_KEY,
        "ts_utc": ts,
        "source_seq": seq,
        "provenance": Provenance.HISTORICAL,
    }


def grid_events() -> list[DomainEvent]:
    """The grid as the stream reports it before lights out."""
    before = START - timedelta(minutes=5)
    return [
        PositionChanged(**_envelope(before, n), driver=driver, position=slot)  # type: ignore[arg-type]
        for n, (driver, slot) in enumerate(GRID.items())
    ]


def driver_list() -> DriverListUpdated:
    return DriverListUpdated(
        **_envelope(START - timedelta(minutes=10), 100),  # type: ignore[arg-type]
        drivers=[DriverRef(number=number, full_name=name) for number, name in NAMES.items()],
    )
