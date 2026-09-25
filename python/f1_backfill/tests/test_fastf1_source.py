"""fastf1_source.extract_tables against a fake SessionLike - no fastf1
network/cache involved, matching this project's "inject at the SessionLike
boundary" testing strategy (see fastf1_source.py's module docstring).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from f1_backfill.fastf1_source import extract_tables


class FakeSession:
    def __init__(
        self,
        laps: pd.DataFrame,
        car_data: Mapping[str, pd.DataFrame],
        weather_data: pd.DataFrame,
        results: pd.DataFrame,
        pos_data: Mapping[str, pd.DataFrame] | None = None,
    ) -> None:
        self.laps = laps
        self.car_data = car_data
        self.pos_data: Mapping[str, pd.DataFrame] = pos_data or {}
        self.weather_data = weather_data
        self.results = results

    def load(self, **_kwargs: Any) -> None:
        raise AssertionError("extract_tables must not call load() - loading is load_session's job")


def _laps() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Driver": ["VER", "VER", "VER", "HAM"],
            "LapNumber": [1, 2, 3, 1],
            "Stint": [1, 1, 2, 1],
            "Compound": ["SOFT", "SOFT", "MEDIUM", "SOFT"],
        }
    )


def test_extract_tables_returns_every_table() -> None:
    session = FakeSession(
        laps=_laps(),
        car_data={"1": pd.DataFrame({"Speed": [300, 310]}), "44": pd.DataFrame({"Speed": [295]})},
        weather_data=pd.DataFrame({"AirTemp": [23.0]}),
        results=pd.DataFrame({"Driver": ["VER", "HAM"], "Position": [1, 2]}),
    )

    tables = extract_tables(session)

    assert set(tables) == {
        "laps",
        "car_telemetry",
        "car_positions",
        "weather",
        "results",
        "stints",
    }
    assert len(tables["laps"]) == 4
    assert len(tables["weather"]) == 1
    assert len(tables["results"]) == 2


def test_car_telemetry_concatenates_all_drivers_with_a_driver_number_column() -> None:
    session = FakeSession(
        laps=_laps(),
        car_data={"1": pd.DataFrame({"Speed": [300, 310]}), "44": pd.DataFrame({"Speed": [295]})},
        weather_data=pd.DataFrame(),
        results=pd.DataFrame(),
    )

    telemetry = extract_tables(session)["car_telemetry"]

    assert len(telemetry) == 3
    assert set(telemetry["DriverNumber"]) == {"1", "44"}


def test_car_telemetry_is_empty_dataframe_when_no_drivers() -> None:
    session = FakeSession(
        laps=_laps(), car_data={}, weather_data=pd.DataFrame(), results=pd.DataFrame()
    )

    telemetry = extract_tables(session)["car_telemetry"]
    assert telemetry.empty


def test_stints_derives_one_row_per_driver_and_stint_with_lap_bounds() -> None:
    session = FakeSession(
        laps=_laps(), car_data={}, weather_data=pd.DataFrame(), results=pd.DataFrame()
    )

    stints = (
        extract_tables(session)["stints"].sort_values(["Driver", "Stint"]).reset_index(drop=True)
    )

    assert list(stints["Driver"]) == ["HAM", "VER", "VER"]
    assert list(stints["Stint"]) == [1, 1, 2]
    assert list(stints["Compound"]) == ["SOFT", "SOFT", "MEDIUM"]
    assert list(stints["LapStart"]) == [1, 1, 3]
    assert list(stints["LapEnd"]) == [1, 2, 3]
    assert list(stints["LapCount"]) == [1, 2, 1]


def test_stints_is_empty_with_expected_columns_when_laps_lacks_required_columns() -> None:
    session = FakeSession(
        laps=pd.DataFrame(), car_data={}, weather_data=pd.DataFrame(), results=pd.DataFrame()
    )

    stints = extract_tables(session)["stints"]
    assert stints.empty
    assert list(stints.columns) == ["Driver", "Stint", "Compound", "LapStart", "LapEnd", "LapCount"]


def test_car_positions_come_from_pos_data() -> None:
    session = FakeSession(
        laps=_laps(),
        car_data={},
        weather_data=pd.DataFrame(),
        results=pd.DataFrame(),
        pos_data={
            "1": pd.DataFrame(
                {
                    "Date": pd.to_datetime(["2024-03-02 15:00:00.000", "2024-03-02 15:00:00.250"]),
                    "X": [100, 120],
                    "Y": [-50, -40],
                    "Z": [3, 3],
                    "Status": ["OnTrack", "OnTrack"],
                }
            )
        },
    )

    positions = extract_tables(session)["car_positions"]

    assert list(positions["X"]) == [100, 120]
    assert set(positions["DriverNumber"]) == {"1"}
