"""The compressed `.z` topics: one message decodes to MULTIPLE samples,
through the real base64+deflate inflation path (payloads are built with
the inverse transform in conftest, not stored as opaque strings)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import JsonValue

from f1_parse import decode_car_data, decode_position

ZEncoder = Callable[[JsonValue], str]

_CAR_DATA_BATCH: JsonValue = {
    "Entries": [
        {
            "Utc": "2024-03-02T15:10:00.1234567Z",
            "Cars": {
                "1": {"Channels": {"0": 11200, "2": 301, "3": 8, "4": 100, "5": 0, "45": 12}},
                "44": {"Channels": {"0": 10850, "2": 287, "3": 7, "4": 87, "5": 0, "45": 8}},
            },
        },
        {
            "Utc": "2024-03-02T15:10:00.3634567Z",
            "Cars": {
                "1": {"Channels": {"0": 11450, "2": 308, "3": 8, "4": 100, "5": 0, "45": 12}},
                "44": {"Channels": {"0": 9200, "2": 251, "3": 6, "4": 0, "5": 100, "45": 8}},
            },
        },
        {
            "Utc": "2024-03-02T15:10:00.6034567Z",
            "Cars": {
                "1": {"Channels": {"0": 11600, "2": 312, "3": 8, "4": 100, "5": 0, "45": 12}},
            },
        },
    ]
}

_POSITION_BATCH: JsonValue = {
    "Position": [
        {
            "Timestamp": "2024-03-02T15:10:00.220Z",
            "Entries": {
                "1": {"Status": "OnTrack", "X": -1523, "Y": 804, "Z": 72},
                "44": {"Status": "OnTrack", "X": -1601, "Y": 823, "Z": 71},
            },
        },
        {
            "Timestamp": "2024-03-02T15:10:00.440Z",
            "Entries": {
                "1": {"Status": "OnTrack", "X": -1490, "Y": 799, "Z": 72},
                "44": {"Status": "OffTrack", "X": -1633, "Y": 851, "Z": 70},
            },
        },
    ]
}


def test_car_data_batch_fans_out_to_one_sample_per_entry_per_driver(encode_z: ZEncoder):
    samples = decode_car_data(encode_z(_CAR_DATA_BATCH))

    # 2 drivers + 2 drivers + 1 driver = 5 samples from ONE message.
    assert len(samples) == 5

    first = samples[0]
    assert first.driver == "1"
    assert first.ts_utc == datetime(2024, 3, 2, 15, 10, 0, 123456, tzinfo=UTC)
    assert first.rpm == 11200
    assert first.speed == 301.0
    assert first.gear == 8
    assert first.throttle == 100.0
    assert first.brake == 0.0
    assert first.drs == 12

    # Each batch entry keeps its own timestamp.
    assert len({s.ts_utc for s in samples}) == 3

    braking_44 = next(s for s in samples if s.driver == "44" and s.brake == 100.0)
    assert braking_44.throttle == 0.0
    assert braking_44.gear == 6


def test_position_batch_fans_out_with_per_entry_timestamps(encode_z: ZEncoder):
    samples = decode_position(encode_z(_POSITION_BATCH))

    assert len(samples) == 4
    assert {s.driver for s in samples} == {"1", "44"}
    assert len({s.ts_utc for s in samples}) == 2

    off_track = next(s for s in samples if s.status == "OffTrack")
    assert off_track.driver == "44"
    assert off_track.x == -1633.0
    assert off_track.ts_utc == datetime(2024, 3, 2, 15, 10, 0, 440000, tzinfo=UTC)


def test_empty_batches_decode_to_no_samples(encode_z: ZEncoder):
    assert decode_car_data(encode_z({"Entries": []})) == []
    assert decode_position(encode_z({"Position": []})) == []
