"""archive-positions: which finished store sessions get their car positions
imported from F1's archive, and that only a complete import is filed.
FastF1 is replaced by an injected loader; the store by plain rows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from f1_domain import SessionKey, SessionType
from f1_historical_adapter.reader import session_directory
from f1_kernel import HistoricalSource
from f1_pipeline.archive import (
    MAX_ATTEMPTS,
    RETRY_BASE,
    RETRY_CAP,
    RetrySchedule,
    StoreSession,
    archive_positions_once,
    sessions_needing_positions,
)
from f1_pipeline.cli import parse_args

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
RACE = SessionKey(year=2026, round=14, session_type=SessionType.RACE)
LAST_LAP = datetime(2026, 9, 13, 14, 35, tzinfo=UTC)


def _session(key: SessionKey = RACE, *, quiet_for: timedelta = timedelta(days=11)) -> StoreSession:
    return StoreSession(key=key, last_event_utc=NOW - quiet_for, last_lap_utc=LAST_LAP)


class _Loader:
    def __init__(self, *, ends: datetime = LAST_LAP + timedelta(minutes=3)) -> None:
        self.calls: list[tuple[int, int, str]] = []
        self.ends = ends

    def __call__(self, year: int, round_no: int, name: str) -> Mapping[str, pd.DataFrame]:
        self.calls.append((year, round_no, name))
        end = self.ends.replace(tzinfo=None)
        return {
            "4": pd.DataFrame(
                {
                    "Date": pd.to_datetime([end - timedelta(seconds=1), end]),
                    "X": [100, 110],
                    "Y": [200, 210],
                    "Z": [1, 1],
                    "Status": ["OnTrack", "OnTrack"],
                }
            )
        }


def test_a_finished_session_is_imported_where_a_replay_looks(tmp_path: Path) -> None:
    loader = _Loader()

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader)

    assert loader.calls == [(2026, 14, "Race")]
    assert report.written == [(RACE, 2)]
    track = HistoricalSource(tmp_path).position_track(RACE)
    assert track is not None and track["x"].to_list() == [100.0, 110.0]


def test_a_session_still_settling_or_long_gone_is_left_alone(tmp_path: Path) -> None:
    sessions = [
        _session(quiet_for=timedelta(minutes=20)),
        _session(
            SessionKey(year=2026, round=2, session_type=SessionType.RACE),
            quiet_for=timedelta(days=90),
        ),
    ]

    assert sessions_needing_positions(sessions, tmp_path, now=NOW) == []


def test_a_session_already_on_file_is_not_imported_again(tmp_path: Path) -> None:
    archive_positions_once([_session()], tmp_path, now=NOW, loader=_Loader())
    loader = _Loader()

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader)

    assert loader.calls == []
    assert report.written == []


def test_a_partial_archive_is_not_filed(tmp_path: Path) -> None:
    loader = _Loader(ends=LAST_LAP - timedelta(minutes=30))

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader)

    assert report.written == []
    assert [key for key, _ in report.pending] == [RACE]
    assert not (session_directory(tmp_path, RACE) / "car_positions.parquet").exists()


@pytest.mark.parametrize("outcome", ["empty", "error"])
def test_an_archive_not_published_yet_stays_pending(tmp_path: Path, outcome: str) -> None:
    def loader(_year: int, _round: int, _name: str) -> Mapping[str, pd.DataFrame]:
        if outcome == "error":
            raise RuntimeError("403 from the archive")
        return {}

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader)

    assert report.written == []
    assert [key for key, _ in report.pending] == [RACE]


def test_sprint_qualifying_uses_fastf1s_session_name(tmp_path: Path) -> None:
    key = SessionKey(year=2026, round=14, session_type=SessionType.SPRINT_QUALIFYING)
    loader = _Loader()

    archive_positions_once([_session(key)], tmp_path, now=NOW, loader=loader)

    assert loader.calls == [(2026, 14, "Sprint Qualifying")]


def test_the_cli_takes_a_watch_interval() -> None:
    args = parse_args(["archive-positions", "--watch", "600"])

    assert args.watch == 600.0
    assert args.settle_minutes == 90.0
    assert parse_args(["archive-positions"]).watch is None


def test_fastf1_giving_up_does_not_stop_the_pass(tmp_path: Path) -> None:
    """FastF1 calls `exit()` when a download returns nothing."""

    def loader(_year: int, _round: int, _name: str) -> Mapping[str, pd.DataFrame]:
        raise SystemExit

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader)

    assert [key for key, _ in report.pending] == [RACE]


def test_a_session_waiting_to_retry_is_skipped(tmp_path: Path) -> None:
    loader = _Loader()

    report = archive_positions_once([_session()], tmp_path, now=NOW, loader=loader, skip={RACE})

    assert loader.calls == []
    assert report.written == report.pending == []


def test_retries_back_off_then_give_up() -> None:
    retries = RetrySchedule()

    assert retries.failed(RACE, NOW) == 1
    assert RACE in retries.waiting(NOW + RETRY_BASE - timedelta(seconds=1))
    assert RACE not in retries.waiting(NOW + RETRY_BASE)

    retries.failed(RACE, NOW)
    assert RACE in retries.waiting(NOW + RETRY_BASE)
    assert RACE not in retries.waiting(NOW + 2 * RETRY_BASE)

    for _ in range(MAX_ATTEMPTS):
        retries.failed(RACE, NOW)
    assert RACE in retries.waiting(NOW + RETRY_CAP * 10)

    retries.succeeded(RACE)
    assert retries.waiting(NOW) == set()
