"""The adapter: chronological ordering, lap counts matching the parquet,
synthetic seq/provenance, and an end-to-end parquet round-trip."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from adapter_data import KEY, laps_frame, race_control_frame, results_frame, weather_frame

from f1_domain import (
    DomainEvent,
    LapCompleted,
    PitEntry,
    PitExit,
    Provenance,
    StintStarted,
)
from f1_historical_adapter import build_events, events_from_parquet
from f1_historical_adapter.reader import SessionTables


def _events(tables: SessionTables) -> list[DomainEvent]:
    return list(build_events(tables))


def test_events_are_in_chronological_order(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    timestamps = [event.ts_utc for event in events]
    assert timestamps == sorted(timestamps)


def test_source_seq_is_a_dense_ordinal(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    assert [event.source_seq for event in events] == list(range(len(events)))


def test_all_events_are_marked_historical(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    assert events
    assert all(event.provenance is Provenance.HISTORICAL for event in events)


def test_lap_counts_per_driver_match_the_parquet(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    laps = [e for e in events if isinstance(e, LapCompleted)]
    per_driver: dict[str, int] = {}
    for lap in laps:
        assert lap.driver is not None
        per_driver[lap.driver] = per_driver.get(lap.driver, 0) + 1

    parquet = laps_frame()
    expected = parquet.groupby("DriverNumber")["LapNumber"].count().to_dict()
    assert per_driver == {str(k): int(v) for k, v in expected.items()}


def test_lap_time_values_survive(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    lap = next(
        e for e in events if isinstance(e, LapCompleted) and e.driver == "1" and e.lap_number == 1
    )
    assert lap.lap_time == timedelta(seconds=90.0)
    assert lap.compound is not None and lap.compound.value == "soft"
    assert lap.is_personal_best is True


def test_stints_and_pits_are_emitted(synthetic_tables: SessionTables) -> None:
    events = _events(synthetic_tables)
    stints = [e for e in events if isinstance(e, StintStarted) and e.driver == "44"]
    assert [s.stint_number for s in stints] == [1, 2]
    assert [s.compound.value for s in stints if s.compound] == ["soft", "hard"]

    pit_entries = [e for e in events if isinstance(e, PitEntry) and e.driver == "44"]
    pit_exits = [e for e in events if isinstance(e, PitExit) and e.driver == "44"]
    assert [p.lap_number for p in pit_entries] == [2]
    assert [p.lap_number for p in pit_exits] == [3]


def test_end_to_end_reads_partitioned_parquet(tmp_path: Path) -> None:
    session_dir = tmp_path / "season=2024" / "round=01" / "session=Race"
    session_dir.mkdir(parents=True)
    laps_frame().to_parquet(session_dir / "laps.parquet", index=False)
    weather_frame().to_parquet(session_dir / "weather.parquet", index=False)
    results_frame().to_parquet(session_dir / "results.parquet", index=False)
    race_control_frame().to_parquet(session_dir / "race_control.parquet", index=False)

    events = list(events_from_parquet(tmp_path, KEY))
    assert events
    # session_start reconstructed from LapStartDate - LapStartTime: the first
    # lap completion should land at 15:01:30Z (90s after the 15:00:00 start).
    laps = [e for e in events if isinstance(e, LapCompleted)]
    assert len(laps) == 6
    assert all(e.provenance is Provenance.HISTORICAL for e in events)


def test_missing_session_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(events_from_parquet(tmp_path, KEY))


def test_the_grid_is_emitted_at_the_session_start(synthetic_tables: SessionTables) -> None:
    """What the live feed reports before lights out, so `f1_why.starting_grid`
    reads a backfilled race like a live one. A car that did not start is left
    out; a pit-lane starter (grid 0) goes behind the grid."""
    import pandas as pd

    from f1_domain import PositionChanged

    results = pd.DataFrame(
        [
            {"DriverNumber": "1", "GridPosition": 2.0, "Status": "Finished"},
            {"DriverNumber": "44", "GridPosition": 1.0, "Status": "Finished"},
            {"DriverNumber": "23", "GridPosition": 3.0, "Status": "Did not start"},
            {"DriverNumber": "18", "GridPosition": 0.0, "Status": "Finished"},
        ]
    )
    tables = SessionTables(
        session_key=synthetic_tables.session_key,
        directory=synthetic_tables.directory,
        tables={"results": results},
        session_start_utc=synthetic_tables.session_start_utc,
    )

    grid = [
        (event.driver, event.position)
        for event in _events(tables)
        if isinstance(event, PositionChanged) and event.ts_utc == tables.session_start_utc
    ]

    assert grid == [("44", 1), ("1", 2), ("18", 3)]
