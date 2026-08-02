"""Shared test data for f1_store: a representative `DomainEvent` stream and
matching `SessionState`, exercising every table and the fan-out case
(several events sharing one `source_seq`).

A uniquely-named module (not `conftest`) so sibling test modules can import
these without colliding with other packages' `conftest` under pytest's
importlib mode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from f1_domain import (
    CarPosition,
    CarTelemetry,
    Compound,
    DomainEvent,
    DriverListUpdated,
    DriverRef,
    DriverState,
    GapUpdated,
    LapCompleted,
    PitEntry,
    PitExit,
    PositionChanged,
    RaceControlMessage,
    SectorCompleted,
    SessionKey,
    SessionState,
    SessionStatusChanged,
    SessionType,
    StintStarted,
    TeamRadioClip,
    TrackStatusChanged,
    TrackStatusKind,
    UnknownEvent,
    WeatherSample,
)

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_TS = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


class _Env(TypedDict):
    session_key: SessionKey
    ts_utc: datetime
    source_seq: int


def _ts(seq: int) -> datetime:
    return _TS + timedelta(seconds=seq)


def _env(seq: int) -> _Env:
    return {"session_key": KEY, "ts_utc": _ts(seq), "source_seq": seq}


def build_sample_events() -> list[DomainEvent]:
    """Events in pipeline emission order, grouped by source_seq — including
    two same-seq fan-out pairs (telemetry, positions)."""
    return [
        DriverListUpdated(
            **_env(1),
            drivers=[
                DriverRef(number="44", code="HAM", team="Mercedes"),
                DriverRef(number="1", code="VER", team="Red Bull Racing"),
            ],
        ),
        SessionStatusChanged(**_env(2), status="Started"),
        TrackStatusChanged(**_env(3), status=TrackStatusKind.GREEN, message="AllClear"),
        WeatherSample(**_env(4), air_temp=24.1, track_temp=41.7, rainfall=False),
        CarTelemetry(**_env(5), driver="44", speed=300.0, rpm=11000, gear=7),
        CarTelemetry(**_env(5), driver="1", speed=305.0, rpm=11200, gear=8),
        CarPosition(**_env(6), driver="44", x=1.0, y=2.0, z=3.0, status="OnTrack"),
        CarPosition(**_env(6), driver="1", x=4.0, y=5.0, z=6.0, status="OnTrack"),
        StintStarted(**_env(7), driver="44", stint_number=1, compound=Compound.SOFT, start_lap=1),
        LapCompleted(
            **_env(8),
            driver="44",
            lap_number=1,
            lap_time=timedelta(seconds=92.5),
            sector_times=[
                timedelta(seconds=30.0),
                timedelta(seconds=31.0),
                timedelta(seconds=31.5),
            ],
            is_personal_best=True,
            compound=Compound.SOFT,
            tyre_age=1,
        ),
        SectorCompleted(
            **_env(9), driver="44", lap_number=2, sector=1, time=timedelta(seconds=29.9)
        ),
        GapUpdated(**_env(10), driver="1", gap_to_leader=timedelta(seconds=1.2)),
        PositionChanged(**_env(11), driver="44", position=1),
        PitEntry(**_env(12), driver="44", lap_number=10),
        PitExit(**_env(13), driver="44", lap_number=11),
        RaceControlMessage(**_env(14), category="Flag", flag="GREEN", scope="Track"),
        TeamRadioClip(
            **_env(15),
            driver="44",
            audio_url="https://livetiming.formula1.com/static/x/TeamRadio/y.mp3",
            captured_at=_ts(15),
        ),
        LapCompleted(**_env(16), driver="1", lap_number=1, lap_time=timedelta(seconds=91.8)),
        UnknownEvent(**_env(17), topic="Heartbeat", payload={"beat": 1}),
    ]


# Expected non-empty row counts after ingesting build_sample_events() once.
EXPECTED_ROW_COUNTS: dict[str, int] = {
    "events": 19,
    "laps": 2,
    "car_telemetry": 2,
    "car_positions": 2,
    "stints": 1,
    "pit_stops": 2,
    "race_control": 1,
    "weather": 1,
    "team_radio": 1,
}


def build_sample_state() -> SessionState:
    return SessionState(
        session_key=KEY,
        status="Started",
        current_lap=1,
        track_status=TrackStatusKind.GREEN,
        drivers={
            "44": DriverState(
                driver=DriverRef(number="44", code="HAM", team="Mercedes"),
                position=1,
                last_lap=timedelta(seconds=92.5),
                best_lap=timedelta(seconds=92.5),
                current_compound=Compound.SOFT,
                tyre_age=1,
                in_pit=False,
                stint_number=1,
                laps_completed=1,
            ),
            "1": DriverState(
                driver=DriverRef(number="1", code="VER", team="Red Bull Racing"),
                position=2,
                gap_to_leader=timedelta(seconds=1.2),
                laps_completed=1,
            ),
        },
    )
