"""State model round-trips: `SessionState`/`DriverState` must survive
JSON serialization losslessly (they mirror the materialized tables)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from f1_domain import (
    Compound,
    DriverRef,
    DriverState,
    SessionKey,
    SessionState,
    SessionType,
    TrackStatusKind,
    WeatherSample,
)

_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)


def test_session_state_roundtrip() -> None:
    state = SessionState(
        session_key=_KEY,
        status="Started",
        current_lap=23,
        track_status=TrackStatusKind.GREEN,
        weather=WeatherSample(
            session_key=_KEY,
            ts_utc=datetime(2024, 3, 2, 15, 30, tzinfo=UTC),
            source_seq=101,
            air_temp=24.0,
            rainfall=False,
        ),
        drivers={
            "1": DriverState(
                driver=DriverRef(number="1", code="VER", team="Red Bull Racing"),
                position=1,
                last_lap=timedelta(minutes=1, seconds=34.2),
                best_lap=timedelta(minutes=1, seconds=32.8),
                current_compound=Compound.HARD,
                tyre_age=11,
                gap_to_leader=timedelta(0),
                laps_down=None,
                interval=None,
                in_pit=False,
                stint_number=2,
                laps_completed=23,
            ),
        },
    )
    restored = SessionState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_empty_session_state_needs_only_a_key() -> None:
    state = SessionState(session_key=_KEY)
    restored = SessionState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.drivers == {}
    assert restored.weather is None
