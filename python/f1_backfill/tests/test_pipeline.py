"""run_backfill: resume/idempotency, --refresh, and per-session failure
isolation - all with injected fakes, no network or fastf1/OpenF1 involved.

This is the acceptance test for the "no redundant network calls on
re-run" requirement: `session_loader` and the OpenF1 `FakeSession` (the
same fake-HTTP-layer pattern as `test_openf1.py`) both count their calls,
and a second `run_backfill` over an unchanged catalog must not add any.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from f1_backfill.fastf1_source import SessionLike
from f1_backfill.openf1 import OpenF1Client
from f1_backfill.pipeline import (
    _intervals_frame,  # pyright: ignore[reportPrivateUsage] - a unit test of an internal
    run_backfill,
)
from f1_backfill.schedule import SessionKey
from f1_backfill.writer import write_tables


class FakeSession:
    def __init__(self) -> None:
        self.laps = pd.DataFrame(
            {"Driver": ["VER"], "LapNumber": [1], "Stint": [1], "Compound": ["SOFT"]}
        )
        self.car_data: Mapping[str, pd.DataFrame] = {}
        self.weather_data = pd.DataFrame({"AirTemp": [23.0]})
        self.results = pd.DataFrame({"Driver": ["VER"], "Position": [1]})

    def load(self, **_kwargs: Any) -> None:
        pass


class RecordingLoader:
    """A fake `session_loader` that counts calls per (season, round, type)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    def __call__(self, season: int, round_no: int, session_type: str) -> SessionLike:
        self.calls.append((season, round_no, session_type))
        return FakeSession()


class FailingLoader(RecordingLoader):
    def __init__(self, *, fail_round: int) -> None:
        super().__init__()
        self._fail_round = fail_round

    def __call__(self, season: int, round_no: int, session_type: str) -> SessionLike:
        super().__call__(season, round_no, session_type)
        if round_no == self._fail_round:
            raise RuntimeError("simulated fastf1 load failure")
        return FakeSession()


class NoMatchOpenF1Session:
    """A fake HTTP session for `OpenF1Client` that always returns an empty
    session list, i.e. no session ever matches - exercises the "fastf1-only,
    no openf1 tables" path without needing a full OpenF1 mock.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        self.calls.append(url)
        return _EmptyResponse()


class _EmptyResponse:
    status_code = 200
    headers: Mapping[str, str] = {}

    def json(self) -> Any:
        return []

    def raise_for_status(self) -> None:
        pass


def _one_round_schedule(_year: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "RoundNumber": 1,
                "EventName": "Bahrain Grand Prix",
                "Location": "Sakhir",
                "Session1": "Race",
                "Session2": None,
                "Session3": None,
                "Session4": None,
                "Session5": None,
            }
        ]
    )


def _two_round_schedule(_year: int) -> pd.DataFrame:
    rows = [
        {
            "RoundNumber": r,
            "EventName": f"Round {r} Grand Prix",
            "Location": f"Circuit {r}",
            "Session1": "Race",
            "Session2": None,
            "Session3": None,
            "Session4": None,
            "Session5": None,
        }
        for r in (1, 2)
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def openf1_client() -> OpenF1Client:
    return OpenF1Client(session=NoMatchOpenF1Session())


def test_first_run_acquires_and_writes_catalog(tmp_path: Path, openf1_client: OpenF1Client) -> None:
    loader = RecordingLoader()
    report = run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    assert len(report.acquired) == 1
    assert report.skipped == []
    assert report.failed == []
    assert loader.calls == [(2024, 1, "Race")]
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "season=2024" / "round=01" / "session=Race" / "laps.parquet").exists()


def test_second_run_skips_and_makes_no_further_loader_or_http_calls(
    tmp_path: Path, openf1_client: OpenF1Client
) -> None:
    loader = RecordingLoader()
    session = NoMatchOpenF1Session()
    client = OpenF1Client(session=session)

    run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=client,
    )
    calls_after_first_run = len(loader.calls)
    http_calls_after_first_run = len(session.calls)

    report = run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=client,
    )

    assert len(loader.calls) == calls_after_first_run  # no new fastf1 "network" calls
    assert len(session.calls) == http_calls_after_first_run  # no new OpenF1 HTTP calls
    assert report.acquired == []
    assert len(report.skipped) == 1


def test_refresh_forces_reacquisition(tmp_path: Path, openf1_client: OpenF1Client) -> None:
    loader = RecordingLoader()
    run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    report = run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
        refresh=True,
    )

    assert len(loader.calls) == 2
    assert len(report.acquired) == 1
    assert report.skipped == []


def test_round_filter_only_acquires_the_requested_round(
    tmp_path: Path, openf1_client: OpenF1Client
) -> None:
    loader = RecordingLoader()
    report = run_backfill(
        2024,
        round_filter=2,
        out_dir=tmp_path,
        schedule_provider=_two_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    assert loader.calls == [(2024, 2, "Race")]
    assert len(report.acquired) == 1


def test_one_failed_session_does_not_prevent_others_from_being_acquired(
    tmp_path: Path, openf1_client: OpenF1Client
) -> None:
    loader = FailingLoader(fail_round=1)
    report = run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_two_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    assert [key.round for key in report.acquired] == [2]
    assert len(report.failed) == 1
    failed_key, message = report.failed[0]
    assert failed_key.round == 1
    assert "simulated fastf1 load failure" in message


def test_no_openf1_match_still_writes_fastf1_only_tables(
    tmp_path: Path, openf1_client: OpenF1Client
) -> None:
    loader = RecordingLoader()
    report = run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    assert len(report.acquired) == 1
    session_dir = tmp_path / "season=2024" / "round=01" / "session=Race"
    assert (session_dir / "laps.parquet").exists()
    assert not (session_dir / "intervals.parquet").exists()


def test_catalog_records_row_counts_and_sources(
    tmp_path: Path, openf1_client: OpenF1Client
) -> None:
    from f1_backfill.catalog import Catalog

    loader = RecordingLoader()
    run_backfill(
        2024,
        out_dir=tmp_path,
        schedule_provider=_one_round_schedule,
        session_loader=loader,
        openf1_client=openf1_client,
    )

    catalog = Catalog.load(tmp_path / "manifest.json")
    key = SessionKey(
        season=2024,
        round=1,
        event_name="Bahrain Grand Prix",
        session_type="Race",
        location="Sakhir",
    )
    entry = catalog.sessions[key.catalog_key]
    assert entry.sources == ["fastf1"]
    assert entry.tables["laps"] == 1


def test_intervals_with_lap_gap_strings_write_to_parquet(tmp_path: Path) -> None:
    """OpenF1 mixes floats and '+1 LAP' strings; parquet must still succeed."""
    key = SessionKey(
        season=2024,
        round=4,
        event_name="Japanese Grand Prix",
        session_type="Race",
        location="Suzuka",
    )
    intervals = _intervals_frame(
        [
            {"driver_number": 1, "gap_to_leader": 0.0, "interval": 0.0},
            {"driver_number": 11, "gap_to_leader": "+1 LAP", "interval": "+1 LAP"},
            {"driver_number": 44, "gap_to_leader": 12.345, "interval": 0.201},
        ]
    )

    row_counts = write_tables(tmp_path, key, {"intervals": intervals})

    assert row_counts == {"intervals": 3}
    path = tmp_path / "season=2024" / "round=04" / "session=Race" / "intervals.parquet"
    roundtrip = pd.read_parquet(path)
    assert list(roundtrip["gap_to_leader"]) == ["0.0", "+1 LAP", "12.345"]
