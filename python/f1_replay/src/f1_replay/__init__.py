"""f1_replay: replays a recording produced by f1_recording at arbitrary speed.

Replays a recording so downstream code receives the SAME record stream, in
the SAME relative timing, as it would live — the fidelity primitive that
makes weekday development possible (see CLAUDE.md's "moat" note). Emits raw
`f1_recording.RawRecord`s only; it does NOT parse or normalize payloads —
that is Phase 1's job, same boundary as the recorder.
"""

from f1_replay.replayer import SUBSCRIBE_SNAPSHOT_TOPIC, Sink, pacing_offset_s, replay, sleep_until
from f1_replay.sinks import jsonl_sink, list_sink

__all__ = [
    "SUBSCRIBE_SNAPSHOT_TOPIC",
    "Sink",
    "jsonl_sink",
    "list_sink",
    "pacing_offset_s",
    "replay",
    "sleep_until",
]

__version__ = "0.0.1"
