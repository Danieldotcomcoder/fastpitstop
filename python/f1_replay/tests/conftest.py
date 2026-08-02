"""Shared test fixtures for f1_replay."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import JsonValue

from f1_recording import Manifest, RecordingReader, RecordingWriter

RecordingFactory = Callable[..., RecordingReader]


def _static_manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "year": 2024,
            "grand_prix": "Test Grand Prix",
            "session_type": "Race",
            "session_start_utc": "2024-01-01T00:00:00Z",
            "recorder_version": "test",
            "topics": ["TimingData"],
        }
    )


@pytest.fixture
def recording_factory(tmp_path: Path) -> RecordingFactory:
    """Return a factory that writes a small synthetic recording and returns
    a `RecordingReader` for it.

    `messages` is a sequence of `(topic, payload, ts_mono)` tuples; `ts_mono`
    is written verbatim (nanoseconds) so tests control inter-message gaps
    precisely instead of depending on real elapsed time between `append()`
    calls.
    """

    def _make(
        messages: Sequence[tuple[str, JsonValue, int]],
        *,
        subscribe_snapshot: JsonValue | None = None,
        directory_name: str = "session",
    ) -> RecordingReader:
        snapshot: JsonValue = (
            subscribe_snapshot
            if subscribe_snapshot is not None
            else {"SessionInfo": {"Meeting": {"Name": "Test Grand Prix"}}}
        )
        directory = tmp_path / directory_name
        with RecordingWriter(directory, _static_manifest(), subscribe_snapshot=snapshot) as writer:
            for topic, payload, ts_mono in messages:
                writer.append(topic, payload, ts_wall=float(ts_mono) / 1e9, ts_mono=ts_mono)
        return RecordingReader(directory)

    return _make
