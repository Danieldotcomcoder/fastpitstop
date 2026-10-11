"""The race calendar (ROADMAP W3): FastF1's schedule reshaped, cached daily,
and served with each round's circuit. No network: FastF1 is faked, and the
routes run on a JSON fixture calendar."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.schedule import (
    ScheduleCache,
    ScheduledEvent,
    ScheduledSession,
    ScheduleUnavailableError,
    fastf1_schedule,
    next_event,
)
from f1_domain import SessionType


def _event(round_number: int, race_start: datetime) -> ScheduledEvent:
    return ScheduledEvent(
        season=race_start.year,
        round=round_number,
        event_name=f"Round {round_number}",
        location="",
        country="",
        event_format="conventional",
        sessions=(
            ScheduledSession(
                "Qualifying", SessionType.QUALIFYING, race_start - timedelta(days=1), None
            ),
            ScheduledSession("Race", SessionType.RACE, race_start, None),
        ),
    )


class TestNextEvent:
    race = datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
    events = (_event(18, race), _event(19, race + timedelta(days=7)))

    def test_this_weekend_until_its_race_could_be_over(self) -> None:
        assert next_event(self.events, self.race - timedelta(days=2)).round == 18  # type: ignore[union-attr]
        assert next_event(self.events, self.race + timedelta(hours=2)).round == 18  # type: ignore[union-attr]

    def test_then_the_next_round(self) -> None:
        assert next_event(self.events, self.race + timedelta(hours=3, minutes=1)).round == 19  # type: ignore[union-attr]

    def test_nothing_left(self) -> None:
        assert next_event(self.events, self.race + timedelta(days=30)) is None


class TestScheduleCache:
    def test_fetches_once_a_day(self) -> None:
        calls: list[int] = []
        now = [0.0]

        def provider(season: int) -> list[ScheduledEvent]:
            calls.append(season)
            return []

        cache = ScheduleCache(provider, ttl_s=100, clock=lambda: now[0])
        cache.events(2026)
        now[0] = 99
        cache.events(2026)
        assert calls == [2026]
        now[0] = 101
        cache.events(2026)
        assert calls == [2026, 2026]

    def test_serves_the_last_good_copy_when_a_refresh_fails(self) -> None:
        now = [0.0]
        good = [_event(1, datetime(2026, 3, 8, 4, tzinfo=UTC))]
        failing = [False]

        def provider(season: int) -> list[ScheduledEvent]:
            if failing[0]:
                raise ConnectionError("offline")
            return good

        cache = ScheduleCache(provider, ttl_s=10, clock=lambda: now[0])
        assert cache.events(2026) == good
        failing[0] = True
        now[0] = 20
        assert cache.events(2026) == good

    def test_raises_when_nothing_was_ever_fetched(self) -> None:
        def provider(season: int) -> list[ScheduledEvent]:
            raise ConnectionError("offline")

        with pytest.raises(ScheduleUnavailableError):
            ScheduleCache(provider).events(2026)


def test_fastf1_schedule_keeps_utc_and_circuit_local_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import fastf1

    local = pd.Timestamp("2026-10-25 15:00:00", tz="America/Chicago")
    frame = pd.DataFrame(
        [
            {
                "RoundNumber": 18,
                "EventName": "United States Grand Prix",
                "Location": "Austin",
                "Country": "United States",
                "EventFormat": "conventional",
                "Session1": "Practice 1",
                "Session1Date": local - pd.Timedelta(days=2),
                "Session1DateUtc": pd.Timestamp("2026-10-23 20:00:00"),
                "Session5": "Race",
                "Session5Date": local,
                "Session5DateUtc": pd.Timestamp("2026-10-25 20:00:00"),
            }
        ]
    )

    def get_event_schedule(season: int, include_testing: bool) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(fastf1, "get_event_schedule", get_event_schedule)
    (event,) = fastf1_schedule(2026)
    assert [s.session_type for s in event.sessions] == [SessionType.PRACTICE_1, SessionType.RACE]
    race = event.sessions[-1]
    assert race.start_utc == datetime(2026, 10, 25, 20, 0, tzinfo=UTC)
    assert race.start_local is not None and race.start_local.utcoffset() == timedelta(hours=-5)


def _fixture(tmp_path: Path, document: dict[str, Any]) -> TestClient:
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return TestClient(create_app(Settings(schedule_fixture_path=path)))


def _session(name: str, start: datetime) -> dict[str, str]:
    return {"name": name, "start_utc": start.isoformat(), "start_local": start.isoformat()}


def test_routes_serve_the_calendar_with_each_rounds_circuit(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    client = _fixture(
        tmp_path,
        {
            str(now.year): [
                {
                    "round": 16,
                    "event_name": "Bahrain Grand Prix",
                    "location": "Kuala Lumpur",
                    "sessions": [_session("Race", now - timedelta(days=7))],
                },
                {
                    "round": 17,
                    "event_name": "Singapore Grand Prix",
                    "location": "Marina Bay",
                    "sessions": [
                        _session("Qualifying", now + timedelta(days=1)),
                        _session("Race", now + timedelta(days=2)),
                    ],
                },
            ]
        },
    )
    calendar = client.get("/schedule", params={"season": now.year}).json()
    assert [(e["round"], e["circuit_id"]) for e in calendar] == [(16, "sepang"), (17, "marina_bay")]
    nxt = client.get("/schedule/next").json()
    assert nxt["round"] == 17
    assert [s["session_type"] for s in nxt["sessions"]] == ["qualifying", "race"]


def test_circuit_by_id(tmp_path: Path) -> None:
    client = _fixture(tmp_path, {})
    marina = client.get("/circuits/marina_bay").json()
    assert marina["name"] == "Marina Bay Street Circuit"
    assert marina["outline"]["points"]
    assert client.get("/circuits/nowhere").status_code == 404
