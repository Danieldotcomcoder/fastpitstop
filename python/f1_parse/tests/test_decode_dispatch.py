"""`decode()` dispatch: right family per topic, and — most importantly —
the never-crash guarantees for unknown topics and malformed payloads."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue

from f1_parse import (
    CarDataSample,
    DecodedMessage,
    Family,
    PositionSample,
    SessionInfoDecoded,
    TimingLinesDelta,
    TrackStatusDecoded,
    WeatherDecoded,
    decode,
)
from f1_recording import RawRecord

ZEncoder = Callable[[JsonValue], str]
RecordFactory = Callable[..., RawRecord]
FixtureLoader = Callable[[str], list[JsonValue]]


def test_topic_to_family_dispatch(
    make_record: RecordFactory, fixture_payloads: FixtureLoader, encode_z: ZEncoder
):
    cases: list[tuple[str, JsonValue, Family]] = [
        ("TimingData", fixture_payloads("timing_data.json")[0], Family.TIMING),
        ("TimingAppData", {"Lines": {"44": {"Stints": {}}}}, Family.TIMING),
        ("TimingStats", {"Lines": {}}, Family.TIMING),
        ("WeatherData", fixture_payloads("weather_data.json")[0], Family.WEATHER),
        ("TrackStatus", fixture_payloads("track_status.json")[0], Family.TRACK_STATUS),
        (
            "RaceControlMessages",
            fixture_payloads("race_control_messages.json")[0],
            Family.RACE_CONTROL,
        ),
        ("TeamRadio", fixture_payloads("team_radio.json")[0], Family.TEAM_RADIO),
        ("SessionInfo", fixture_payloads("session_info.json")[0], Family.SESSION_INFO),
        (
            "SessionData",
            {"StatusSeries": [{"Utc": "2024-03-02T15:03:00Z", "SessionStatus": "Started"}]},
            Family.SESSION_DATA,
        ),
        ("LapCount", {"CurrentLap": 12, "TotalLaps": 57}, Family.LAP_COUNT),
        ("DriverList", fixture_payloads("driver_list.json")[0], Family.DRIVER_LIST),
        ("CarData.z", encode_z({"Entries": []}), Family.CAR_DATA),
        ("Position.z", encode_z({"Position": []}), Family.POSITION),
    ]
    for topic, payload, expected_family in cases:
        message = decode(make_record(topic, payload, seq=9))
        assert message.family == expected_family, topic
        assert message.topic == topic
        assert message.source_seq == 9


def test_decoded_data_types_match_family(make_record: RecordFactory, encode_z: ZEncoder):
    assert isinstance(decode(make_record("TimingData", {"Lines": {}})).data, TimingLinesDelta)
    assert isinstance(decode(make_record("WeatherData", {})).data, WeatherDecoded)
    assert isinstance(decode(make_record("TrackStatus", {})).data, TrackStatusDecoded)
    assert isinstance(decode(make_record("SessionInfo", {})).data, SessionInfoDecoded)

    car_samples = decode(
        make_record(
            "CarData.z",
            encode_z({"Entries": [{"Utc": "2024-01-01T00:00:00Z", "Cars": {"1": {}}}]}),
        )
    ).data
    assert isinstance(car_samples, list)
    assert all(isinstance(s, CarDataSample) for s in car_samples)

    pos_samples = decode(
        make_record(
            "Position.z",
            encode_z({"Position": [{"Timestamp": "2024-01-01T00:00:00Z", "Entries": {"1": {}}}]}),
        )
    ).data
    assert isinstance(pos_samples, list)
    assert all(isinstance(s, PositionSample) for s in pos_samples)


def test_unknown_topic_passes_through_verbatim(make_record: RecordFactory):
    payload: JsonValue = {"Some": ["new", "shape"]}
    message: DecodedMessage = decode(make_record("BrandNewTopic", payload, seq=3))
    assert message.family == Family.UNKNOWN
    assert message.data == payload
    assert message.topic == "BrandNewTopic"
    assert message.source_seq == 3


def test_meta_sentinels_pass_through_as_meta(make_record: RecordFactory):
    snapshot: JsonValue = {"SessionInfo": {"Meeting": {"Name": "X"}}}
    message = decode(make_record("_meta.subscribe_snapshot", snapshot))
    assert message.family == Family.META
    assert message.data == snapshot

    reconnect = decode(make_record("_meta.reconnect", {"attempt": 2}))
    assert reconnect.family == Family.META


def test_malformed_compressed_payload_degrades_not_raises(make_record: RecordFactory):
    # Not valid base64/deflate — the CarData decoder itself would raise;
    # decode() must contain it and pass the payload through.
    message = decode(make_record("CarData.z", "!!!not-base64!!!"))
    assert message.family == Family.UNKNOWN
    assert message.data == "!!!not-base64!!!"


def test_compressed_topic_with_non_string_payload_degrades(make_record: RecordFactory):
    message = decode(make_record("CarData.z", {"unexpected": "dict"}))
    assert message.family == Family.UNKNOWN
    assert message.data == {"unexpected": "dict"}


def test_wrong_shaped_payload_on_known_topic_still_decodes(make_record: RecordFactory):
    # Tolerant decoders: a garbage TimingData payload yields an empty
    # delta rather than an exception or an UNKNOWN downgrade.
    message = decode(make_record("TimingData", [1, 2, 3]))
    assert message.family == Family.TIMING
    data = message.data
    assert isinstance(data, TimingLinesDelta)
    assert data.lines == {}
