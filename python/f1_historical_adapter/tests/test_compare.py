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


def test_a_live_session_is_compared_lap_by_lap_with_the_archive() -> None:
    """`compare_event_streams`, the check run after every live session:
    the tyre on each lap (unknowns skipped), its age within a lap, laps
    completed, stops begun after lap 1, and the final order."""
    from f1_domain import Compound, PositionChanged, StintStarted
    from f1_historical_adapter import compare_event_streams

    def lap(driver: str, number: int, compound: Compound, age: int, live: bool) -> LapCompleted:
        return LapCompleted(
            session_key=_KEY,
            ts_utc=_TS,
            source_seq=0,
            provenance=Provenance.LIVE if live else Provenance.HISTORICAL,
            driver=driver,
            lap_number=number,
            lap_time=timedelta(seconds=90),
            compound=compound,
            tyre_age=age,
        )

    def stint(driver: str, number: int, start: int | None) -> StintStarted:
        return StintStarted(
            session_key=_KEY,
            ts_utc=_TS,
            source_seq=0,
            driver=driver,
            stint_number=number,
            start_lap=start,
        )

    def place(driver: str, position: int) -> PositionChanged:
        return PositionChanged(
            session_key=_KEY, ts_utc=_TS, source_seq=0, driver=driver, position=position
        )

    archive: list[DomainEvent] = [
        stint("81", 1, 1),
        stint("81", 2, 3),
        lap("81", 1, Compound.HARD, 1, False),
        lap("81", 2, Compound.HARD, 2, False),
        lap("81", 3, Compound.MEDIUM, 1, False),
        lap("81", 4, Compound.MEDIUM, 2, False),
        place("81", 1),
        place("4", 2),
    ]
    live: list[DomainEvent] = [
        stint("81", 1, None),  # opened before the first lap was counted
        stint("81", 2, 3),
        lap("81", 1, Compound.HARD, 0, True),  # age a lap behind: agrees
        lap("81", 2, Compound.UNKNOWN, 2, True),  # unnamed: not compared
        lap("81", 3, Compound.HARD, 5, True),  # wrong tyre, wrong age
        place("81", 2),
        place("4", 1),
    ]

    report = compare_event_streams(_KEY, live, archive)
    by_name = {agreement.name: agreement for agreement in report.agreements}

    assert (by_name["compound"].matched, by_name["compound"].mismatched) == (1, 1)
    assert by_name["compound"].only_historical == 2  # lap 2 unnamed live, lap 4 not run
    assert (by_name["tyre_age"].matched, by_name["tyre_age"].mismatched) == (2, 1)
    assert by_name["laps_completed"].mismatched == 1  # 3 live, 4 in the archive
    assert by_name["stops"].matched == 1  # one stop each side
    assert by_name["final_positions"].mismatched == 2
    assert "driver 81 lap 3" in by_name["compound"].examples[0]
