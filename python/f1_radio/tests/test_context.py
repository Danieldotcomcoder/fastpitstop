"""`SessionContext.at`: race context (lap number, position, track status) at
one instant, derived purely from a session's own event stream -- no store,
no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radio_data import KEY

from f1_domain import (
    DomainEvent,
    LapCompleted,
    PositionChanged,
    RaceControlMessage,
    TrackStatusKind,
)
from f1_radio.context import SessionContext

_T0 = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def _t(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _lap(driver: str, lap_number: int, at_s: int, seq: int) -> LapCompleted:
    return LapCompleted(
        session_key=KEY, ts_utc=_t(at_s), source_seq=seq, driver=driver, lap_number=lap_number
    )


def _pos(driver: str, position: int, at_s: int, seq: int) -> PositionChanged:
    return PositionChanged(
        session_key=KEY, ts_utc=_t(at_s), source_seq=seq, driver=driver, position=position
    )


def _rc(
    *, category: str, at_s: int, seq: int, message: str = "", flag: str = "", scope: str = ""
) -> RaceControlMessage:
    return RaceControlMessage(
        session_key=KEY,
        ts_utc=_t(at_s),
        source_seq=seq,
        category=category,
        message=message,
        flag=flag,
        scope=scope,
    )


def _events() -> list[DomainEvent]:
    return [
        _pos("44", 3, at_s=50, seq=0),
        _lap("44", 1, at_s=100, seq=1),
        _rc(category="SafetyCar", message="SAFETY CAR DEPLOYED", at_s=120, seq=2),
        _pos("44", 2, at_s=150, seq=3),
        _rc(category="Flag", flag="CLEAR", scope="Track", message="TRACK CLEAR", at_s=180, seq=4),
        _lap("44", 2, at_s=200, seq=5),
    ]


def test_lap_in_progress_before_any_completed_lap_is_lap_one() -> None:
    ctx = SessionContext(_events())
    result = ctx.at("44", _t(90))
    assert result.lap_number == 1


def test_lap_in_progress_after_a_completed_lap_increments() -> None:
    ctx = SessionContext(_events())
    result = ctx.at("44", _t(160))
    assert result.lap_number == 2


def test_position_is_the_last_known_value_at_or_before_capture() -> None:
    ctx = SessionContext(_events())
    assert ctx.at("44", _t(90)).position == 3
    assert ctx.at("44", _t(160)).position == 2


def test_position_is_none_before_any_position_update() -> None:
    ctx = SessionContext(_events())
    assert ctx.at("44", _t(10)).position is None


def test_track_status_reflects_the_open_safety_car_window() -> None:
    ctx = SessionContext(_events())
    assert ctx.at("44", _t(90)).track_status == TrackStatusKind.GREEN
    assert ctx.at("44", _t(160)).track_status == TrackStatusKind.SC
    assert TrackStatusKind.SC in ctx.at("44", _t(160)).track_status_flags
    assert ctx.at("44", _t(190)).track_status == TrackStatusKind.GREEN


def test_unknown_driver_or_captured_at_returns_unknown_context() -> None:
    ctx = SessionContext(_events())
    assert ctx.at(None, _t(90)).lap_number is None
    assert ctx.at("44", None).lap_number is None


def test_driver_with_no_events_at_all_still_answers() -> None:
    ctx = SessionContext(_events())
    result = ctx.at("1", _t(90))
    assert result.lap_number == 1
    assert result.position is None
    assert result.track_status == TrackStatusKind.GREEN
