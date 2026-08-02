"""f1_recording: the on-disk recording format contract.

This package is the CONTRACT between the recorder and the replayer — see
the "Hard architectural invariants" section of CLAUDE.md. No other package
should invent its own recording format. It knows nothing about F1 domain
semantics beyond raw topic names; parsing payloads into laps, stints, gaps,
etc. is Phase 1's job.

A recording is a directory containing `manifest.json`, `subscribe.json`,
and `stream.jsonl` — see docs/DATA_CONTRACTS.md for the full spec.
"""

from f1_recording.codec import decode_z
from f1_recording.models import FORMAT_VERSION, Manifest, RawRecord
from f1_recording.reader import RecordingReader
from f1_recording.writer import RecordingWriter

__all__ = [
    "FORMAT_VERSION",
    "Manifest",
    "RawRecord",
    "RecordingReader",
    "RecordingWriter",
    "decode_z",
]
