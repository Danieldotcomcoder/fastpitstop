"""RecordingReader: lazily reads a recording written by RecordingWriter."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pydantic import JsonValue, ValidationError

from f1_recording.models import (
    MANIFEST_FILENAME,
    STREAM_FILENAME,
    SUBSCRIBE_FILENAME,
    Manifest,
    RawRecord,
)


class RecordingReader:
    """Reads a recording directory: manifest, subscribe snapshot, and stream."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._manifest = Manifest.model_validate_json(
            (self._directory / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        self._subscribe_snapshot: JsonValue = json.loads(
            (self._directory / SUBSCRIBE_FILENAME).read_text(encoding="utf-8")
        )

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    @property
    def subscribe_snapshot(self) -> JsonValue:
        return self._subscribe_snapshot

    def records(self) -> Iterator[RawRecord]:
        """Yield RawRecords in receive order.

        Tolerates a truncated/partial final line — the on-disk trace of a
        crash mid-write. Only the very last line may be malformed; if it is,
        it's silently dropped instead of raising. Any earlier malformed line
        is a genuine corruption and raises.
        """
        stream_path = self._directory / STREAM_FILENAME
        with stream_path.open("r", encoding="utf-8") as f:
            pending: str | None = None
            for line in f:
                if pending is not None:
                    yield _parse_record(pending)
                pending = line
            if pending is not None and pending.strip():
                try:
                    yield _parse_record(pending)
                except (json.JSONDecodeError, ValidationError):
                    return


def _parse_record(line: str) -> RawRecord:
    return RawRecord.model_validate_json(line)
