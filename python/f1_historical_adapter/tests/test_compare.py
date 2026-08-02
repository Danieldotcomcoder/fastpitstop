"""The comparison harness core (`compare_streams`) — pure, so it needs no
recording or parquet, just two event streams and a state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from f1_domain import (
    DomainEvent,
    DriverRef,
    DriverState,
    LapCompleted,
    PitEntry,
    Provenance,
    SessionKey,
    SessionState,
    SessionType,
)
from f1_historical_adapter import compare_streams

_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_TS = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def _lap(driver: str, lap: int, seconds: float, *, historical: bool = False) -> LapCompleted:
    return LapCompleted(
        session_key=_KEY,
        ts_utc=_TS,
        source_seq=0,
        provenance=Provenance.HISTORICAL if historical else Provenance.LIVE,
        driver=driver,
        lap_number=lap,
        lap_time=timedelta(seconds=seconds),
    )


def _pit(driver: str, lap: int) -> PitEntry:
    return PitEntry(session_key=_KEY, ts_utc=_TS, source_seq=0, driver=driver, lap_number=lap)


def _state(positions: dict[str, int]) -> SessionState:
    return SessionState(
        session_key=_KEY,
        drivers={
            number: DriverState(driver=DriverRef(number=number), position=pos)
            for number, pos in positions.items()
        },
    )


def test_identical_streams_agree_fully() -> None:
    live: list[DomainEvent] = [_lap("1", 1, 90.0), _lap("1", 2, 91.0), _pit("1", 1)]
    hist: list[DomainEvent] = [
        _lap("1", 1, 90.0, historical=True),
        _lap("1", 2, 91.0, historical=True),
        _pit("1", 1),
    ]
    report = compare_streams(_KEY, live, _state({"1": 1}), hist)
    by_name = {a.name: a for a in report.agreements}
    assert by_name["lap_times"].matched == 2
    assert by_name["lap_times"].mismatched == 0
    assert by_name["lap_times"].fraction == 1.0
    assert by_name["pit_laps"].matched == 1


def test_lap_time_within_tolerance_still_agrees() -> None:
    live: list[DomainEvent] = [_lap("1", 1, 90.000)]
    hist: list[DomainEvent] = [_lap("1", 1, 90.030, historical=True)]  # 30ms < 50ms tol
    report = compare_streams(_KEY, live, _state({}), hist)
    lap_agreement = next(a for a in report.agreements if a.name == "lap_times")
    assert lap_agreement.matched == 1
    assert lap_agreement.mismatched == 0


def test_discrepancies_are_reported_not_raised() -> None:
    live: list[DomainEvent] = [_lap("1", 1, 90.0), _lap("44", 1, 93.0)]
    hist: list[DomainEvent] = [
        _lap("1", 1, 95.0, historical=True),  # 5s off -> mismatch
        _lap("99", 1, 88.0, historical=True),  # only historical
    ]
    report = compare_streams(_KEY, live, _state({"1": 1, "44": 2}), hist)
    laps = next(a for a in report.agreements if a.name == "lap_times")
    assert laps.mismatched == 1
    assert laps.only_live == 1  # driver 44 lap 1
    assert laps.only_historical == 1  # driver 99 lap 1
    assert laps.examples  # a sample discrepancy is described
    # Rendering must not raise and should mention the session.
    assert "2024" in report.render()


def test_final_positions_compared_against_state() -> None:
    live: list[DomainEvent] = []
    from f1_domain import PositionChanged

    hist: list[DomainEvent] = [
        PositionChanged(session_key=_KEY, ts_utc=_TS, source_seq=0, driver="1", position=1),
        PositionChanged(session_key=_KEY, ts_utc=_TS, source_seq=1, driver="44", position=2),
    ]
    report = compare_streams(_KEY, live, _state({"1": 1, "44": 3}), hist)
    positions = next(a for a in report.agreements if a.name == "final_positions")
    assert positions.matched == 1  # driver 1 agrees
    assert positions.mismatched == 1  # driver 44: live 3 vs historical 2
