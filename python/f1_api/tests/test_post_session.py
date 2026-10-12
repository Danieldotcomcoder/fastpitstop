"""The post-session check (`f1_api.post_session`): what viewers were shown
live, against F1's archive. Run on the checked-in fixture race with fakes
for the store and the archive download: no database, no network."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from api_fixture import HISTORICAL_ROOT

from f1_api.post_session import (
    COMPARED_KINDS,
    Backoff,
    check_once,
    file_archive,
    findings,
    report_path,
)
from f1_domain import Compound, DomainEvent, LapCompleted, SessionKey, SessionType
from f1_historical_adapter import compare_event_streams, events_from_parquet

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
NOW = datetime(2024, 3, 3, 12, 0, tzinfo=UTC)
FINISHED = NOW - timedelta(hours=3)


@pytest.fixture
def historical(tmp_path: Path) -> Path:
    """A copy of the fixture's backfill, which the check may write to."""
    root = tmp_path / "historical"
    shutil.copytree(HISTORICAL_ROOT, root)
    return root


def _archive_events() -> list[DomainEvent]:
    return [e for e in events_from_parquet(HISTORICAL_ROOT, KEY) if e.kind in COMPARED_KINDS]


def _never_fetch(key: SessionKey) -> tuple[Any, dict[str, pd.DataFrame]]:
    raise AssertionError(f"{key} is already in the backfill; nothing should be downloaded")


def test_a_session_that_matches_the_archive_is_reported_once_without_an_error(
    historical: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reports = tmp_path / "post_session"

    def run() -> Any:
        return check_once(
            lambda: [(KEY, FINISHED)],
            lambda _key: _archive_events(),
            historical,
            reports,
            _never_fetch,
            now=NOW,
        )

    first = run()
    assert first.checked == [(KEY, [])]
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    text = report_path(reports, KEY).read_text(encoding="utf-8")
    assert "matches F1's archive" in text
    assert report_path(reports, KEY).with_suffix(".json").is_file()
    # Reported once: the next pass leaves it alone.
    assert run().checked == []


def test_wrong_tyres_live_are_reported_and_logged(
    historical: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The bug this exists for: the live tower showing another compound.
    Logged at ERROR, one line per difference (logs only, no email)."""
    live: list[DomainEvent] = []
    for event in _archive_events():
        if isinstance(event, LapCompleted) and event.lap_number and event.lap_number > 20:
            wrong = Compound.HARD if event.compound != Compound.HARD else Compound.SOFT
            event = event.model_copy(update={"compound": wrong})
        live.append(event)

    result = check_once(
        lambda: [(KEY, FINISHED)],
        lambda _key: live,
        historical,
        tmp_path / "post_session",
        _never_fetch,
        now=NOW,
    )

    ((_, found),) = result.checked
    assert any("tyre compounds" in line for line in found)
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("2024 round 1 race" in line and "tyre compounds" in line for line in errors)


def test_sessions_still_settling_or_long_past_are_left_alone(
    historical: Path, tmp_path: Path
) -> None:
    sessions = [(KEY, NOW - timedelta(minutes=30)), (KEY, NOW - timedelta(days=30))]
    result = check_once(
        lambda: sessions,
        lambda _key: _archive_events(),
        historical,
        tmp_path / "post_session",
        _never_fetch,
        now=NOW,
    )
    assert result.checked == [] and result.pending == []


def test_practice_is_judged_on_its_laps_only() -> None:
    """Stops and places mean nothing in practice; a race allows no driver
    to differ on them."""
    from f1_domain import PositionChanged

    swapped: list[DomainEvent] = [
        e.model_copy(update={"position": 21 - e.position})
        if isinstance(e, PositionChanged) and e.position
        else e
        for e in _archive_events()
    ]
    race = compare_event_streams(KEY, swapped, _archive_events())
    practice_key = KEY.model_copy(update={"session_type": SessionType.PRACTICE_1})
    practice = compare_event_streams(practice_key, swapped, _archive_events())

    assert any("final places" in line for line in findings(race))
    assert findings(practice) == []


class _NoOpenF1:
    def find_session_key(self, **_: object) -> None:
        return None


def test_an_archive_is_filed_only_once_it_is_complete(tmp_path: Path) -> None:
    from f1_backfill.schedule import SessionKey as BackfillKey

    root = tmp_path / "historical"
    root.mkdir()
    key = SessionKey(year=2026, round=17, session_type=SessionType.RACE)
    backfill_key = BackfillKey(2026, 17, "Singapore Grand Prix", "Race", "Marina Bay")
    results = pd.DataFrame({"DriverNumber": ["1"], "Position": [1.0]})

    def fetch_laps(last: int) -> Any:
        laps = pd.DataFrame({"DriverNumber": ["1"] * last, "LapNumber": range(1, last + 1)})

        def fetch(_key: SessionKey) -> tuple[Any, dict[str, pd.DataFrame]]:
            return backfill_key, {"laps": laps, "results": results}

        return fetch

    partial = file_archive(key, root, fetch_laps(40), last_lap=62, now=NOW, openf1=_NoOpenF1())
    assert partial == "the archive reaches lap 40, viewers saw lap 62"
    assert not (root / "manifest.json").exists()

    assert file_archive(key, root, fetch_laps(62), last_lap=62, now=NOW, openf1=_NoOpenF1()) is None
    manifest = (root / "manifest.json").read_text(encoding="utf-8")
    assert "2026_17_Race" in manifest
    assert (root / "season=2026" / "round=17" / "session=Race" / "laps.parquet").is_file()


def test_a_missing_archive_is_retried_later_and_later() -> None:
    backoff = Backoff()
    backoff.missed(KEY, NOW)
    assert KEY in backoff.waiting(NOW + timedelta(minutes=29))
    assert KEY not in backoff.waiting(NOW + timedelta(minutes=31))
    backoff.missed(KEY, NOW)
    assert KEY in backoff.waiting(NOW + timedelta(minutes=59))
    backoff.done(KEY)
    assert backoff.waiting(NOW) == set()
