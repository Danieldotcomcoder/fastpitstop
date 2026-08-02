"""The replayer: turns a recording back into the SAME record stream a live
consumer would have seen, in the SAME relative timing.

This is the fidelity primitive Phase 1's pipeline (and every phase after it)
develops against on weekdays: `replay()` must be indistinguishable from a
live `f1_ingest` connection from the point of view of whatever consumes its
records, modulo wall-clock speed. It does NOT parse or normalize payloads —
it emits raw `f1_recording.RawRecord`s, exactly like the recorder wrote them.

Timing model
------------
Every record in `stream.jsonl` carries `ts_mono`, the monotonic-clock
nanosecond timestamp at receive time (see docs/DATA_CONTRACTS.md). Replay
measures each record's *elapsed* time since the first stream record
(`ts_mono[i] - ts_mono[0]`) and schedules its emission at:

    replay_start + delay + elapsed / speed

relative to `replay_start` (the moment `replay()` was called). Two
consequences fall out of that formula on purpose:

- `speed` only scales the *gaps* between messages, never the fixed `delay`
  offset — `delay` shifts the whole stream later by a constant, it does not
  compound per message. That's what makes it the right primitive for Phase
  3's "Delay Sync" (hold data back to match a delayed broadcast): the offset
  is constant regardless of how fast/slow the stream underneath it replays.
- `speed=0` means "as fast as possible": the `elapsed / speed` term is
  dropped entirely (not computed as a division by zero) so records are
  emitted back-to-back, still after the initial `delay`.

The initial subscribe snapshot (see `docs/DATA_CONTRACTS.md`'s
`subscribe.json`) is surfaced first, before any stream record, mirroring
live connect order (a real consumer always gets the full-state snapshot
before the first incremental delta). It has no `seq` of its own in the
on-disk format — `subscribe.json` lives outside `stream.jsonl`'s numbering
— so it is wrapped in a synthetic `RawRecord` using the same sentinel-topic
convention `f1_ingest.RecordingSession` uses for *resubscribe* snapshots on
reconnect (`SUBSCRIBE_SNAPSHOT_TOPIC`, i.e. `"_meta.subscribe_snapshot"`),
so downstream code can recognize "this is a full snapshot, not a delta" by
topic alone regardless of whether it came from initial connect or a
reconnect. It is scheduled at the same elapsed=0 instant as the first
stream record (both are emitted immediately after `delay`, snapshot first)
since the recording format does not capture how much real time separated
the snapshot arriving from the first delta.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from f1_recording import RawRecord, RecordingReader

SUBSCRIBE_SNAPSHOT_TOPIC = "_meta.subscribe_snapshot"
"""Sentinel topic for the synthetic record wrapping the initial subscribe
snapshot. Matches `f1_ingest.recording_session.RESUBSCRIBE_SNAPSHOT_TOPIC`'s
naming convention (PascalCase-free, leading underscore) for the same reason:
unmistakably synthetic, never a real F1 topic.
"""

Sink = Callable[[RawRecord], None]


def replay(
    recording: Path | RecordingReader,
    sink: Sink,
    *,
    speed: float = 1.0,
    delay: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Replay a recording through `sink`, in seq order, at `speed` and with
    a fixed `delay`.

    - `speed`: time multiplier. `1` = real time, `100` = 100x fast,
      `0` = as-fast-as-possible (no inter-message sleeping at all).
    - `delay`: hold every emission back by this many extra seconds, as a
      constant offset (see the module docstring's timing model) — the
      Phase 3 "Delay Sync" primitive.
    - `clock`/`sleep`: injectable for tests; default to the real monotonic
      clock and `time.sleep`.

    The subscribe snapshot is sent to `sink` first (see module docstring),
    then every stream record in seq order. Raises `ValueError` if `speed` is
    negative — time doesn't run backwards here.
    """
    if speed < 0:
        raise ValueError(f"speed must be >= 0, got {speed}")

    reader = recording if isinstance(recording, RecordingReader) else RecordingReader(recording)
    start = clock()

    sleep_until(start, delay, clock=clock, sleep=sleep)
    sink(_snapshot_record(reader))

    base_ts_mono: int | None = None
    for record in reader.records():
        if base_ts_mono is None:
            base_ts_mono = record.ts_mono
            target_offset = delay
        else:
            elapsed_s = (record.ts_mono - base_ts_mono) / 1_000_000_000
            target_offset = pacing_offset_s(elapsed_s, speed=speed, delay=delay)
        sleep_until(start, target_offset, clock=clock, sleep=sleep)
        sink(record)


def pacing_offset_s(elapsed_s: float, *, speed: float, delay: float) -> float:
    """The timing model's core formula, shared by every consumer that paces
    a timestamped stream the way this module paces `RawRecord`s: how long
    after `delay` an item `elapsed_s` past the stream's first item should be
    emitted. `speed <= 0` means as-fast-as-possible (no scaling term at
    all) — see the module docstring for the full model this implements.
    """
    return delay + (elapsed_s / speed if speed > 0 else 0.0)


def sleep_until(
    start: float,
    target_offset: float,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Block until `start + target_offset`, per `clock`. Public so other
    consumers of this module's timing model (e.g. `f1_api`'s replay-as-live
    WebSocket mode, which paces already-normalized `DomainEvent`s by
    `ts_utc` rather than `RawRecord`s by `ts_mono`) share this exact
    primitive instead of re-deriving it — "one implementation", the same
    discipline `f1_recording`/`f1_replay` apply to the recording format
    itself (CLAUDE.md)."""
    remaining = (start + target_offset) - clock()
    if remaining > 0:
        sleep(remaining)


def _snapshot_record(reader: RecordingReader) -> RawRecord:
    """Wrap the recording's subscribe snapshot in a synthetic RawRecord.

    `seq=0` is a placeholder, not a claim of uniqueness against the real
    stream's seq numbering: `subscribe.json` was never assigned a seq (it
    precedes `stream.jsonl` entirely, per the on-disk format), and `seq`'s
    `ge=0` constraint leaves no integer that could sit "before" the real
    seq 0. Consumers must rely on *emission order* (this is always sent
    first) rather than comparing `seq` values across the synthetic
    snapshot and real stream records.
    """
    return RawRecord(
        seq=0,
        ts_wall=time.time(),
        ts_mono=time.monotonic_ns(),
        topic=SUBSCRIBE_SNAPSHOT_TOPIC,
        payload=reader.subscribe_snapshot,
    )
