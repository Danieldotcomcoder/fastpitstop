"""Sinks for `replay()`: what to do with each emitted `RawRecord`.

The default sink (used by the CLI) prints JSONL to a stream, one record per
line, in the exact on-disk shape `f1_recording.RawRecord.model_dump_json`
produces — so a replayed session piped through `f1-replay` looks the same
on the wire as `stream.jsonl` itself, just with the synthetic subscribe
snapshot record prepended.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from f1_recording import RawRecord
from f1_replay.replayer import Sink


def jsonl_sink(stream: TextIO = sys.stdout) -> Sink:
    """Return a sink that writes each record as one JSON line to `stream`."""

    def _sink(record: RawRecord) -> None:
        stream.write(record.model_dump_json())
        stream.write("\n")
        stream.flush()

    return _sink


def list_sink(out: list[RawRecord]) -> Callable[[RawRecord], None]:
    """Return a sink that appends every record to `out`, in emission order.

    Handy for tests and for any caller that wants to buffer a whole replay
    (e.g. `speed=0`) instead of streaming it record-by-record.
    """

    def _sink(record: RawRecord) -> None:
        out.append(record)

    return _sink
