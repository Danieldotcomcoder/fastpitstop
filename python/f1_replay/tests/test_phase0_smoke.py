"""The Phase 0 end-to-end smoke test — the canary Phase 1 builds on.

Exercises the full happy path exactly the way a Phase 1 pipeline will
consume it: `f1_recording.RecordingWriter` writes a session to disk, then
`f1_replay.replay` streams it back through the Python sink API, and we
assert count and order. Nothing is faked below the sink: real files, the
real reader, the real replayer. If this breaks, everything downstream of
Phase 0 is broken too.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from f1_recording import Manifest, RawRecord, RecordingWriter
from f1_replay.replayer import SUBSCRIBE_SNAPSHOT_TOPIC, replay
from f1_replay.sinks import list_sink

_MESSAGES: list[tuple[str, JsonValue]] = [
    ("TrackStatus", {"Status": "1"}),
    ("TimingData", {"Lines": {"1": {"GapToLeader": "+0.5"}}}),
    ("CarData.z", "eJxLzs8rzs9J1XPMS8xJLE4FAB0JBKE="),
    ("TimingData", {"Lines": {"1": {"GapToLeader": "+0.7"}}}),
    ("WeatherData", {"AirTemp": "21.0"}),
]


def test_record_then_replay_via_sink_api(tmp_path: Path) -> None:
    directory = tmp_path / "smoke_session"
    snapshot: JsonValue = {"SessionInfo": {"Meeting": {"Name": "Smoke Grand Prix"}}}
    manifest = Manifest.model_validate(
        {
            "year": 2024,
            "grand_prix": "Smoke Grand Prix",
            "session_type": "Race",
            "session_start_utc": "2024-01-01T00:00:00Z",
            "recorder_version": "smoke-test",
            "topics": sorted({topic for topic, _ in _MESSAGES}),
        }
    )

    # 1. Record: write the session to disk (real recording directory).
    with RecordingWriter(directory, manifest, subscribe_snapshot=snapshot) as writer:
        for topic, payload in _MESSAGES:
            writer.append(topic, payload)
    assert (directory / "manifest.json").exists()
    assert (directory / "subscribe.json").exists()
    assert (directory / "stream.jsonl").exists()

    # 2. Replay: consume it through the Python sink API at speed=0.
    emitted: list[RawRecord] = []
    replay(directory, list_sink(emitted), speed=0)

    # 3. Count: snapshot + every recorded message, nothing more or less.
    assert len(emitted) == len(_MESSAGES) + 1

    # 4. Order: snapshot strictly first, then the stream in write order
    #    with contiguous seq numbers and untouched topic/payload pairs.
    assert emitted[0].topic == SUBSCRIBE_SNAPSHOT_TOPIC
    assert emitted[0].payload == snapshot
    for seq, (record, (topic, payload)) in enumerate(zip(emitted[1:], _MESSAGES, strict=True)):
        assert record.seq == seq
        assert record.topic == topic
        assert record.payload == payload
