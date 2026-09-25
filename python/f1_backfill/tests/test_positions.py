"""f1_backfill.positions: the car_positions table and the --positions-only
retrofit, with FastF1 replaced by an injected loader (no network, no cache)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from f1_backfill.catalog import Catalog
from f1_backfill.positions import position_table, run_positions
from f1_backfill.schedule import SessionKey


def _samples(xs: list[int], *, start: str = "2024-03-02 15:00:00") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(xs), freq="250ms")
    return pd.DataFrame(
        {
            "Date": dates,
            "Status": "OnTrack",
            "X": xs,
            "Y": [x * 2 for x in xs],
            "Z": [5] * len(xs),
            "Source": "pos",
        }
    )


def test_table_merges_drivers_in_time_order() -> None:
    table = position_table({"44": _samples([10, 20]), "1": _samples([11, 21])})

    assert list(table.columns) == ["DriverNumber", "Date", "X", "Y", "Z", "Status"]
    assert list(table["DriverNumber"]) == ["1", "44", "1", "44"]
    assert list(table["X"]) == [11, 10, 21, 20]
    assert table["Date"].is_monotonic_increasing


def test_garage_placeholder_rows_are_dropped() -> None:
    samples = _samples([0, 0, 30])
    samples.loc[:1, ["Y", "Z"]] = 0

    table = position_table({"1": samples})

    assert list(table["X"]) == [30]


def test_a_real_point_on_an_axis_is_kept() -> None:
    samples = _samples([0])
    samples["Y"] = [150]

    assert len(position_table({"1": samples})) == 1


def test_no_samples_gives_an_empty_typed_table() -> None:
    table = position_table({"1": pd.DataFrame()})

    assert table.empty
    assert list(table.columns) == ["DriverNumber", "Date", "X", "Y", "Z", "Status"]


def _schedule(_season: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "RoundNumber": 1,
                "EventName": "Bahrain Grand Prix",
                "Location": "Sakhir",
                "Session1": "Qualifying",
                "Session2": "Race",
            }
        ]
    )


class _Loader:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    def __call__(self, season: int, round_no: int, session_type: str) -> Mapping[str, pd.DataFrame]:
        self.calls.append((season, round_no, session_type))
        return {"1": _samples([100, 110, 120])}


def _catalog_with_race(out: Path) -> Path:
    catalog = Catalog()
    catalog.record(
        SessionKey(2024, 1, "Bahrain Grand Prix", "Race", "Sakhir"),
        sources=["fastf1"],
        tables={"laps": 57},
        acquired_at=datetime(2024, 3, 3, tzinfo=UTC),
    )
    path = out / "manifest.json"
    catalog.save(path)
    return path


def test_retrofit_writes_catalogued_sessions_and_records_the_table(tmp_path: Path) -> None:
    catalog_path = _catalog_with_race(tmp_path)
    loader = _Loader()

    report = run_positions(2024, out_dir=tmp_path, schedule_provider=_schedule, loader=loader)

    # Qualifying is not in the catalog, so only the race is touched.
    assert loader.calls == [(2024, 1, "Race")]
    assert [(key.session_type, rows) for key, rows in report.written] == [("Race", 3)]
    written = pd.read_parquet(tmp_path / "season=2024/round=01/session=Race/car_positions.parquet")
    assert list(written["X"]) == [100, 110, 120]
    entry = Catalog.load(catalog_path).sessions["2024_01_Race"]
    assert entry.tables == {"laps": 57, "car_positions": 3}


def test_retrofit_skips_a_table_that_already_exists(tmp_path: Path) -> None:
    _catalog_with_race(tmp_path)
    run_positions(2024, out_dir=tmp_path, schedule_provider=_schedule, loader=_Loader())
    loader = _Loader()

    report = run_positions(2024, out_dir=tmp_path, schedule_provider=_schedule, loader=loader)

    assert loader.calls == []
    assert len(report.skipped) == 2


def test_an_uncatalogued_session_can_be_targeted_explicitly(tmp_path: Path) -> None:
    loader = _Loader()

    report = run_positions(
        2024,
        session_filter="Race",
        include_uncatalogued=True,
        out_dir=tmp_path,
        schedule_provider=_schedule,
        loader=loader,
    )

    assert loader.calls == [(2024, 1, "Race")]
    assert len(report.written) == 1
    # Positions only: the session is still not a catalogued historical one.
    assert Catalog.load(tmp_path / "manifest.json").sessions == {}


def test_a_session_without_positions_is_reported_not_written(tmp_path: Path) -> None:
    _catalog_with_race(tmp_path)

    def nothing(_season: int, _round: int, _session: str) -> Mapping[str, pd.DataFrame]:
        return {}

    report = run_positions(2024, out_dir=tmp_path, schedule_provider=_schedule, loader=nothing)

    assert [key.session_type for key, _ in report.failed] == ["Race"]
    assert not (tmp_path / "season=2024/round=01/session=Race/car_positions.parquet").exists()
