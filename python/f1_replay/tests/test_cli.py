"""CLI smoke tests: `f1-replay` prints the snapshot + stream as JSONL."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from contextlib import redirect_stdout

from f1_recording import RecordingReader
from f1_replay.cli import main, parse_args

RecordingFactory = Callable[..., RecordingReader]


def test_parse_args_defaults() -> None:
    args = parse_args(["some/recording"])
    assert args.speed == 1.0
    assert args.delay == 0.0


def test_parse_args_overrides() -> None:
    args = parse_args(["some/recording", "--speed", "100", "--delay", "2.5"])
    assert args.speed == 100.0
    assert args.delay == 2.5


def test_main_prints_snapshot_then_stream_as_jsonl(recording_factory: RecordingFactory) -> None:
    messages = [
        ("TrackStatus", {"Status": "1"}, 1_000_000_000),
        ("WeatherData", {"AirTemp": "20"}, 1_100_000_000),
    ]
    reader = recording_factory(messages)

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main([str(reader.directory), "--speed", "0"])

    assert exit_code == 0
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3  # snapshot + 2 stream records

    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["topic"] == "_meta.subscribe_snapshot"
    assert parsed[0]["payload"] == reader.subscribe_snapshot
    assert [p["topic"] for p in parsed[1:]] == ["TrackStatus", "WeatherData"]
    assert [p["seq"] for p in parsed[1:]] == [0, 1]
