"""schedule.sessions_for_season: enumerates (season, round, session type)
tuples from a fastf1-shaped event schedule DataFrame - no network, via an
injected `schedule_provider`.
"""

from __future__ import annotations

import math

import pandas as pd

from f1_backfill.schedule import SessionKey, sessions_for_season


def _fake_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "RoundNumber": 0,  # pre-season testing - must be excluded
                "EventName": "Pre-Season Testing",
                "Location": "Sakhir",
                "Session1": "Practice 1",
                "Session2": math.nan,
                "Session3": math.nan,
                "Session4": math.nan,
                "Session5": math.nan,
            },
            {
                "RoundNumber": 1,
                "EventName": "Bahrain Grand Prix",
                "Location": "Sakhir",
                "Session1": "Practice 1",
                "Session2": "Practice 2",
                "Session3": "Practice 3",
                "Session4": "Qualifying",
                "Session5": "Race",
            },
            {
                "RoundNumber": 2,
                "EventName": "Saudi Arabian Grand Prix",
                "Location": "Jeddah",
                "Session1": "Practice 1",
                "Session2": "Sprint Qualifying",
                "Session3": "Sprint",
                "Session4": "Qualifying",
                "Session5": "Race",
            },
        ]
    )


def test_excludes_round_zero_and_lists_all_sessions_for_remaining_rounds() -> None:
    keys = sessions_for_season(2024, schedule_provider=lambda _year: _fake_schedule())

    rounds = {key.round for key in keys}
    assert rounds == {1, 2}
    assert len(keys) == 10  # 5 sessions each for round 1 and round 2


def test_round_filter_restricts_to_one_round() -> None:
    keys = sessions_for_season(
        2024, round_filter=2, schedule_provider=lambda _year: _fake_schedule()
    )

    assert {key.round for key in keys} == {2}
    assert [key.session_type for key in keys] == [
        "Practice 1",
        "Sprint Qualifying",
        "Sprint",
        "Qualifying",
        "Race",
    ]


def test_session_key_fields_are_populated_correctly() -> None:
    keys = sessions_for_season(
        2024, round_filter=1, schedule_provider=lambda _year: _fake_schedule()
    )

    race = next(k for k in keys if k.session_type == "Race")
    assert race == SessionKey(
        season=2024,
        round=1,
        event_name="Bahrain Grand Prix",
        session_type="Race",
        location="Sakhir",
    )


def test_catalog_key_is_stable_and_slugified() -> None:
    key = SessionKey(
        season=2024,
        round=1,
        event_name="Bahrain Grand Prix",
        session_type="Practice 1",
        location="Sakhir",
    )
    assert key.catalog_key == "2024_01_Practice_1"
