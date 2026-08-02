"""RecordingWriter: creates a recording directory and appends RawRecords.

Fsync-on-flush semantics: `flush()` — called by default on every `append` —
flushes userspace buffers and fsyncs the underlying file descriptor, so a
crash mid-race loses at most whatever was written since the last flush,
never more. Pass `sync=False` to `append` to batch writes for throughput and
call `flush()` yourself on your own cadence.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import JsonValue

from f1_recording.models import (
    MANIFEST_FILENAME,
    STREAM_FILENAME,
    SUBSCRIBE_FILENAME,
    Manifest,
    RawRecord,
)


class RecordingWriter:
    """Writes one recording: manifest + subscribe snapshot + append-only stream.

    `subscribe_snapshot` is required up front (not a separate call) because
    a recording without it is incomplete per the format — the recorder
    always has it in hand before the stream starts (it's the full-state
    message the feed sends on subscribe, i.e. before any incremental
    updates arrive).
    """

    def __init__(self, directory: Path, manifest: Manifest, subscribe_snapshot: JsonValue) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._manifest = manifest
        self._next_seq = 0
        (self._directory / MANIFEST_FILENAME).write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        (self._directory / SUBSCRIBE_FILENAME).write_text(
            json.dumps(subscribe_snapshot), encoding="utf-8"
        )
        self._stream_file = (self._directory / STREAM_FILENAME).open(
            "a", encoding="utf-8", newline="\n"
        )

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def append(
        self,
        topic: str,
        payload: JsonValue,
        *,
        ts_wall: float | None = None,
        ts_mono: int | None = None,
        sync: bool = True,
    ) -> RawRecord:
        """Append one raw message, assigning it the next monotonic seq.

        `ts_wall`/`ts_mono` default to the current wall clock / monotonic
        clock; callers only need to override them in tests or when replaying
        timestamps from another source.
        """
        record = RawRecord(
            seq=self._next_seq,
            ts_wall=ts_wall if ts_wall is not None else time.time(),
            ts_mono=ts_mono if ts_mono is not None else time.monotonic_ns(),
            topic=topic,
            payload=payload,
        )
        self._stream_file.write(record.model_dump_json() + "\n")
        self._next_seq += 1
        if sync:
            self.flush()
        return record

    def flush(self) -> None:
        """Flush userspace buffers and fsync the file descriptor to disk."""
        self._stream_file.flush()
        os.fsync(self._stream_file.fileno())

    def close(self) -> None:
        if not self._stream_file.closed:
            self.flush()
            self._stream_file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
