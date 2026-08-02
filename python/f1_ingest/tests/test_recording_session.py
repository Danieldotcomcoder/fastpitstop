"""RecordingSession fed a canned SignalR message sequence — no real socket.

This is the acceptance test for f1_ingest: given the kind of subscribe
snapshot and feed messages the real feed would send, a well-formed
recording directory must come out the other end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from f1_ingest.recording_session import (
    RECONNECT_MARKER_TOPIC,
    RESUBSCRIBE_SNAPSHOT_TOPIC,
    RecordingSession,
    RecordingSessionError,
)
from f1_recording import RecordingReader, decode_z


def _z_encode(value: Any) -> str:
    import base64
    import json
    import zlib

    raw = json.dumps(value).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(raw) + compressor.flush()
    return base64.b64encode(compressed).decode("ascii")


def test_full_session_produces_well_formed_recording(
    tmp_path: Path, canned_subscribe_snapshot: dict[str, Any]
) -> None:
    topics = ["TimingData", "CarData.z", "WeatherData"]
    session = RecordingSession(tmp_path, topics=topics, recorder_version="test-1.0")

    session.on_subscribe_result(canned_subscribe_snapshot)
    session.on_feed_message("TimingData", {"Lines": {"1": {"Position": "1"}}})
    car_data_payload = _z_encode({"Entries": [{"Cars": {"1": {"Channels": {"0": 11000}}}}]})
    session.on_feed_message("CarData.z", car_data_payload)
    session.on_feed_message("WeatherData", {"AirTemp": "23.5"})
    session.close()

    directory = session.directory
    assert directory is not None
    assert directory.parent == tmp_path
    assert (directory / "manifest.json").exists()
    assert (directory / "subscribe.json").exists()
    assert (directory / "stream.jsonl").exists()

    reader = RecordingReader(directory)
    assert reader.manifest.grand_prix == "Belgian Grand Prix"
    assert reader.manifest.session_type == "Race"
    assert reader.manifest.year == 2024
    assert reader.manifest.recorder_version == "test-1.0"
    assert reader.manifest.topics == topics
    assert reader.subscribe_snapshot == canned_subscribe_snapshot

    records = list(reader.records())
    assert [r.topic for r in records] == ["TimingData", "CarData.z", "WeatherData"]
    assert [r.seq for r in records] == [0, 1, 2]
    assert records[0].payload == {"Lines": {"1": {"Position": "1"}}}

    car_data_record_payload = records[1].payload
    assert isinstance(car_data_record_payload, str)
    assert car_data_record_payload == car_data_payload
    assert decode_z(car_data_record_payload) == {
        "Entries": [{"Cars": {"1": {"Channels": {"0": 11000}}}}]
    }

    assert records[2].payload == {"AirTemp": "23.5"}


def test_directory_name_uses_session_name_override(
    tmp_path: Path, canned_subscribe_snapshot: dict[str, Any]
) -> None:
    session = RecordingSession(
        tmp_path, topics=[], recorder_version="test", session_name="My Custom Name!"
    )
    session.on_subscribe_result(canned_subscribe_snapshot)
    session.close()

    assert session.directory is not None
    assert session.directory.name == "My_Custom_Name"


def test_directory_name_default_from_manifest(
    tmp_path: Path, canned_subscribe_snapshot: dict[str, Any]
) -> None:
    session = RecordingSession(tmp_path, topics=[], recorder_version="test")
    session.on_subscribe_result(canned_subscribe_snapshot)
    session.close()

    assert session.directory is not None
    assert session.directory.name == "2024_Belgian_Grand_Prix_Race"


def test_feed_message_before_subscribe_raises(tmp_path: Path) -> None:
    session = RecordingSession(tmp_path, topics=[], recorder_version="test")
    with pytest.raises(RecordingSessionError):
        session.on_feed_message("TimingData", {})


def test_reconnect_appends_marker_and_fresh_snapshot_without_new_directory(
    tmp_path: Path, canned_subscribe_snapshot: dict[str, Any]
) -> None:
    session = RecordingSession(tmp_path, topics=["TimingData"], recorder_version="test")

    session.on_subscribe_result(canned_subscribe_snapshot)
    first_directory = session.directory
    session.on_feed_message("TimingData", {"a": 1})

    # Simulate a reconnect: the feed sends a fresh subscribe completion.
    fresh_snapshot: dict[str, Any] = {**canned_subscribe_snapshot, "TrackStatus": {"Status": "2"}}
    session.on_subscribe_result(fresh_snapshot)
    session.on_feed_message("TimingData", {"a": 2})
    session.close()

    # A reconnect must not start a new recording directory or rewrite the
    # original manifest/subscribe.json - sessions can span reconnects.
    assert session.directory == first_directory
    assert first_directory is not None
    reader = RecordingReader(first_directory)
    assert reader.subscribe_snapshot == canned_subscribe_snapshot

    records = list(reader.records())
    topics = [r.topic for r in records]
    assert topics == [
        "TimingData",
        RECONNECT_MARKER_TOPIC,
        RESUBSCRIBE_SNAPSHOT_TOPIC,
        "TimingData",
    ]
    assert records[1].payload == {"attempt": 1}
    assert records[2].payload == fresh_snapshot
    assert [r.seq for r in records] == [0, 1, 2, 3]


def test_multiple_reconnects_increment_attempt_counter(
    tmp_path: Path, canned_subscribe_snapshot: dict[str, Any]
) -> None:
    session = RecordingSession(tmp_path, topics=[], recorder_version="test")
    session.on_subscribe_result(canned_subscribe_snapshot)
    session.on_subscribe_result(canned_subscribe_snapshot)
    session.on_subscribe_result(canned_subscribe_snapshot)
    session.close()

    assert session.directory is not None
    reader = RecordingReader(session.directory)
    markers = [r for r in reader.records() if r.topic == RECONNECT_MARKER_TOPIC]
    assert [m.payload for m in markers] == [{"attempt": 1}, {"attempt": 2}]
