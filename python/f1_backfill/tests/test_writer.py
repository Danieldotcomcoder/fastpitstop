"""writer.write_tables: partition layout + parquet schema fidelity."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from f1_backfill.schedule import SessionKey
from f1_backfill.writer import session_dir, write_tables

_KEY = SessionKey(
    season=2024,
    round=1,
    event_name="Bahrain Grand Prix",
    session_type="Practice 1",
    location="Sakhir",
)


def test_session_dir_uses_hive_style_partitioning() -> None:
    directory = session_dir(Path("data/historical"), _KEY)
    assert directory == Path("data/historical/season=2024/round=01/session=Practice_1")


def test_write_tables_writes_parquet_with_matching_schema_and_row_counts(tmp_path: Path) -> None:
    laps = pd.DataFrame({"Driver": ["VER", "HAM"], "LapNumber": [1, 1], "LapTime": [91.2, 91.5]})
    weather = pd.DataFrame({"AirTemp": [23.4], "TrackTemp": [31.2]})

    row_counts = write_tables(tmp_path, _KEY, {"laps": laps, "weather": weather})

    assert row_counts == {"laps": 2, "weather": 1}

    directory = session_dir(tmp_path, _KEY)
    laps_roundtrip = pd.read_parquet(directory / "laps.parquet")
    weather_roundtrip = pd.read_parquet(directory / "weather.parquet")

    pd.testing.assert_frame_equal(laps_roundtrip, laps)
    pd.testing.assert_frame_equal(weather_roundtrip, weather)


def test_write_tables_skips_columnless_tables(tmp_path: Path) -> None:
    row_counts = write_tables(
        tmp_path, _KEY, {"laps": pd.DataFrame(), "weather": pd.DataFrame({"AirTemp": [1.0]})}
    )

    assert row_counts == {"weather": 1}
    directory = session_dir(tmp_path, _KEY)
    assert not (directory / "laps.parquet").exists()
    assert (directory / "weather.parquet").exists()
