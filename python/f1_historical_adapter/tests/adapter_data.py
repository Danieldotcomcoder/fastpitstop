"""Synthetic historical tables for the adapter tests.

Small hand-built frames with the same columns FastF1/OpenF1 produce (schema
verified against a real 2024 Bahrain pull), so the adapter is exercised
without a real backfill on disk. A uniquely-named module (not `conftest`)
so sibling tests can import it without a cross-package name clash under
pytest's importlib mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from f1_domain import SessionKey, SessionType
from f1_historical_adapter.reader import SessionTables

SESSION_START = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)
KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)

# Two drivers, three laps each; driver 44 pits on lap 2 (new stint on lap 3).
_LAPS = [
    # driver, lap, end_s, laptime_s, stint, compound, tyrelife, pos, pit_in_s, pit_out_s
    ("1", 1, 90.0, 90.0, 1, "SOFT", 1, 1, None, None),
    ("1", 2, 181.0, 91.0, 1, "SOFT", 2, 1, None, None),
    ("1", 3, 273.0, 92.0, 1, "SOFT", 3, 1, None, None),
    ("44", 1, 93.0, 93.0, 1, "SOFT", 1, 2, None, None),
    ("44", 2, 186.0, 93.0, 1, "SOFT", 2, 2, 185.0, None),
    ("44", 3, 279.0, 93.0, 2, "HARD", 1, 2, None, 187.0),
]


def _td(seconds: float | None) -> Any:
    """A pandas Timedelta, or NaT for None (to mimic FastF1's missing cells)."""
    return pd.NaT if seconds is None else pd.to_timedelta(seconds, unit="s")


def laps_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for driver, lap, end_s, laptime_s, stint, compound, tyrelife, pos, pit_in, pit_out in _LAPS:
        lap_start_s = end_s - laptime_s
        rows.append(
            {
                "DriverNumber": driver,
                "LapNumber": float(lap),
                "Time": _td(end_s),
                "LapTime": _td(laptime_s),
                "LapStartTime": _td(lap_start_s),
                # absolute start = session_start (naive UTC) + relative start
                "LapStartDate": pd.Timestamp("2024-03-02 15:00:00") + _td(lap_start_s),
                "Sector1Time": _td(laptime_s / 3),
                "Sector2Time": _td(laptime_s / 3),
                "Sector3Time": _td(laptime_s / 3),
                "Sector1SessionTime": _td(lap_start_s + laptime_s / 3),
                "Sector2SessionTime": _td(lap_start_s + 2 * laptime_s / 3),
                "Sector3SessionTime": _td(end_s),
                "IsPersonalBest": lap == 1,
                "Compound": compound,
                "TyreLife": float(tyrelife),
                "Stint": float(stint),
                "Position": float(pos),
                "PitInTime": _td(pit_in),
                "PitOutTime": _td(pit_out),
            }
        )
    return pd.DataFrame(rows)


def weather_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Time": _td(10.0),
                "AirTemp": 24.0,
                "TrackTemp": 40.0,
                "Humidity": 40.0,
                "Pressure": 1012.0,
                "WindSpeed": 1.0,
                "WindDirection": 180,
                "Rainfall": False,
            },
            {
                "Time": _td(70.0),
                "AirTemp": 24.5,
                "TrackTemp": 41.0,
                "Humidity": 39.0,
                "Pressure": 1012.1,
                "WindSpeed": 1.2,
                "WindDirection": 190,
                "Rainfall": False,
            },
        ]
    )


def results_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "DriverNumber": "1",
                "Abbreviation": "VER",
                "TeamName": "Red Bull Racing",
                "Position": 1.0,
            },
            {"DriverNumber": "44", "Abbreviation": "HAM", "TeamName": "Mercedes", "Position": 2.0},
        ]
    )


def race_control_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2024-03-02T15:00:05+00:00",
                "driver_number": float("nan"),
                "lap_number": 1,
                "category": "Flag",
                "flag": "GREEN",
                "scope": "Track",
                "message": "GREEN LIGHT",
            },
        ]
    )


def build_synthetic_tables() -> SessionTables:
    return SessionTables(
        session_key=KEY,
        directory=Path("."),
        tables={
            "laps": laps_frame(),
            "weather": weather_frame(),
            "results": results_frame(),
            "race_control": race_control_frame(),
        },
        session_start_utc=SESSION_START,
    )
