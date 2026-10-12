"""Serialization round-trip for every event type, and discriminated-union
resolution from JSON — the two properties that make `DomainEvent` a usable
contract (an event log you can't read back losslessly is not a log).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TypedDict, get_args

import pytest
from pydantic import JsonValue

from f1_domain import (
    DOMAIN_EVENT_ADAPTER,
    CarPosition,
    CarTelemetry,
    Compound,
    DomainEvent,
    DriverListUpdated,
    DriverRef,
    EventBase,
    GapUpdated,
    LapCompleted,
    LapCounterUpdated,
    PitEntry,
    PitExit,
    PositionChanged,
    QualifyingBestLap,
    QualifyingKnockout,
    QualifyingPartChanged,
    RaceControlMessage,
    SectorCompleted,
    SessionClockUpdated,
    SessionInfoUpdated,
    SessionKey,
    SessionStatusChanged,
    SessionType,
    StintStarted,
    TeamRadioClip,
    TrackStatusChanged,
    TrackStatusKind,
    UnknownEvent,
    WeatherSample,
)

_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_TS = datetime(2024, 3, 2, 15, 30, 0, tzinfo=UTC)


class _Envelope(TypedDict):
    session_key: SessionKey
    ts_utc: datetime
    source_seq: int


def _envelope(seq: int = 7) -> _Envelope:
    return {"session_key": _KEY, "ts_utc": _TS, "source_seq": seq}


# One fully-populated example per event type. Every event class in the
# union MUST appear here — test_every_union_member_has_an_example enforces
# it, so adding an event without a round-trip test fails loudly.
_UNKNOWN_PAYLOAD: JsonValue = {"Nested": {"Field": [1, None, "x"]}}

EXAMPLES: Sequence[EventBase] = [
    LapCompleted(
        **_envelope(),
        driver="44",
        lap_number=12,
        lap_time=timedelta(minutes=1, seconds=23.456),
        sector_times=[timedelta(seconds=28.1), None, timedelta(seconds=27.9)],
        is_personal_best=True,
        compound=Compound.MEDIUM,
        tyre_age=8,
    ),
    SectorCompleted(
        **_envelope(), driver="1", lap_number=12, sector=2, time=timedelta(seconds=27.301)
    ),
    CarTelemetry(
        **_envelope(),
        driver="16",
        speed=312.0,
        rpm=11250,
        gear=8,
        throttle=100.0,
        brake=0.0,
        drs=12,
    ),
    CarPosition(**_envelope(), driver="16", x=-1523.0, y=804.5, z=7.2, status="OnTrack"),
    PositionChanged(**_envelope(), driver="4", position=3),
    GapUpdated(
        **_envelope(),
        driver="4",
        gap_to_leader=timedelta(seconds=5.132),
        interval_to_ahead=timedelta(seconds=0.874),
    ),
    # The lapped-car form: laps behind is a COUNT, never a duration, and it
    # excludes gap_to_leader (the feed sends "1 L" instead of a time).
    GapUpdated(
        **_envelope(),
        driver="24",
        gap_to_leader=None,
        laps_down=1,
        interval_to_ahead=timedelta(seconds=12.402),
    ),
    PitEntry(**_envelope(), driver="55", lap_number=18),
    PitExit(**_envelope(), driver="55", lap_number=19),
    StintStarted(
        **_envelope(),
        driver="55",
        stint_number=2,
        compound=Compound.HARD,
        start_lap=19,
        tyre_age_at_start=2,
    ),
    TrackStatusChanged(**_envelope(), status=TrackStatusKind.SC, message="SCDeployed"),
    RaceControlMessage(
        **_envelope(),
        category="Flag",
        message="DOUBLE YELLOW IN SECTOR 7",
        flag="DOUBLE YELLOW",
        scope="Sector",
        driver=None,
        lap=18,
    ),
    WeatherSample(
        **_envelope(),
        air_temp=24.1,
        track_temp=41.7,
        humidity=38.0,
        pressure=1012.3,
        wind_speed=2.4,
        wind_dir=210.0,
        rainfall=False,
    ),
    TeamRadioClip(
        **_envelope(),
        driver="44",
        audio_url="TeamRadio/HAMLEW01_44_20240302_153000.mp3",
        captured_at=_TS,
    ),
    SessionStatusChanged(**_envelope(), status="Started"),
    LapCounterUpdated(**_envelope(), current_lap=21, total_laps=57),
    SessionClockUpdated(**_envelope(), remaining=timedelta(minutes=12, seconds=4), running=True),
    QualifyingPartChanged(**_envelope(), part=2, entries=[22, 16, 10]),
    QualifyingBestLap(
        **_envelope(), driver="16", part=2, lap_time=timedelta(seconds=87.533), lap_number=7
    ),
    QualifyingKnockout(**_envelope(), driver="77", part=1),
    SessionInfoUpdated(
        **_envelope(), meeting_name="Azerbaijan Grand Prix", circuit_short_name="Baku"
    ),
    DriverListUpdated(
        **_envelope(),
        drivers=[
            DriverRef(number="44", code="HAM", team="Mercedes"),
            DriverRef(number="1", code="VER", team="Red Bull Racing"),
        ],
    ),
    UnknownEvent(**_envelope(), topic="SomeBrandNewTopic", payload=_UNKNOWN_PAYLOAD),
]


@pytest.mark.parametrize("event", EXAMPLES, ids=lambda e: type(e).__name__)
def test_json_roundtrip_is_lossless(event: EventBase) -> None:
    restored = DOMAIN_EVENT_ADAPTER.validate_json(event.model_dump_json())
    assert type(restored) is type(event)
    assert restored == event


@pytest.mark.parametrize("event", EXAMPLES, ids=lambda e: type(e).__name__)
def test_union_resolves_from_plain_json_dict(event: EventBase) -> None:
    # From a plain dict (as it would come out of a DB json column), not a
    # pydantic-produced string: discrimination must work on `kind` alone.
    restored = DOMAIN_EVENT_ADAPTER.validate_python(event.model_dump(mode="json"))
    assert type(restored) is type(event)
    assert restored == event


def test_every_union_member_has_an_example() -> None:
    union_members = set(get_args(get_args(DomainEvent)[0]))
    example_types = {type(e) for e in EXAMPLES}
    assert example_types == union_members


def test_kind_values_are_unique_across_the_union() -> None:
    # Keyed by class: a class may have several examples (e.g. GapUpdated's
    # lapped-car form), but two CLASSES must never share a `kind`.
    kind_by_type = {type(e): e.model_dump(mode="json")["kind"] for e in EXAMPLES}
    assert len(set(kind_by_type.values())) == len(kind_by_type)


def test_minimal_events_only_need_the_envelope() -> None:
    # Optional-by-default: the feed drops fields constantly, so an event
    # with nothing but the envelope must be constructible and round-trip.
    minimal = LapCompleted(session_key=_KEY, ts_utc=_TS, source_seq=0)
    restored = DOMAIN_EVENT_ADAPTER.validate_json(minimal.model_dump_json())
    assert restored == minimal
    assert isinstance(restored, LapCompleted)
    assert restored.lap_time is None


def test_events_are_frozen() -> None:
    event = PitEntry(**_envelope(), driver="55", lap_number=18)
    with pytest.raises(Exception, match="frozen"):
        event.driver = "44"  # type: ignore[misc]


def test_provenance_defaults_to_live_and_round_trips() -> None:
    from f1_domain import Provenance

    live = LapCompleted(session_key=_KEY, ts_utc=_TS, source_seq=0)
    assert live.provenance is Provenance.LIVE

    historical = LapCompleted(
        session_key=_KEY, ts_utc=_TS, source_seq=0, provenance=Provenance.HISTORICAL
    )
    restored = DOMAIN_EVENT_ADAPTER.validate_json(historical.model_dump_json())
    assert isinstance(restored, LapCompleted)
    assert restored.provenance is Provenance.HISTORICAL
    # A default-provenance event and an explicit-LIVE one are equal: the
    # live pipeline never has to set it.
    assert live == LapCompleted(
        session_key=_KEY, ts_utc=_TS, source_seq=0, provenance=Provenance.LIVE
    )


def test_unknown_event_preserves_payload_verbatim() -> None:
    payload: JsonValue = {"A": [1, 2, {"B": None}], "C": "x"}
    event = UnknownEvent(**_envelope(), topic="Mystery", payload=payload)
    restored = DOMAIN_EVENT_ADAPTER.validate_json(event.model_dump_json())
    assert isinstance(restored, UnknownEvent)
    assert restored.topic == "Mystery"
    assert restored.payload == payload
