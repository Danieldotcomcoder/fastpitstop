"""Turn a recording into a pull iterable of `RawRecord`s for the pipeline.

`f1_replay.replay` is push-based (it calls a sink callback) and owns the
replay semantics — subscribe-snapshot-first, seq order, `--speed`/`--delay`
timing. The pipeline's `run` is pull-based. This bridges the two with a
bounded queue and a producer thread, so `f1_replay` stays the single
implementation of replay semantics (a hard invariant) while the pipeline
consumes a streaming iterator (a whole race never sits in memory at once).

Record ORDER out is exactly `f1_replay`'s emission order (one producer,
FIFO queue) — deterministic. Timing (`speed`) is wall-clock only and never
touches an event, so it does not affect the event stream.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from queue import Queue

from f1_recording import RawRecord, RecordingReader
from f1_replay.replayer import replay

_QUEUE_MAX = 10_000  # backpressure: the producer blocks when the pipeline lags


class _Done:
    """Sentinel marking the end of the record stream."""


def replay_records(
    recording: Path | RecordingReader, *, speed: float = 0.0, delay: float = 0.0
) -> Iterator[RawRecord]:
    """Yield a recording's records (subscribe snapshot first, then seq order)
    at `speed`/`delay`, as a streaming iterator suitable for `pipeline.run`.

    `speed=0` (the default here — ingesting a recording wants to go as fast
    as possible) emits back-to-back with no sleeping.
    """
    queue: Queue[RawRecord | _Done] = Queue(maxsize=_QUEUE_MAX)
    error: list[BaseException] = []

    def produce() -> None:
        try:
            replay(recording, queue.put, speed=speed, delay=delay)
        except BaseException as exc:  # re-raised on the consumer side below
            error.append(exc)
        finally:
            queue.put(_Done())

    thread = threading.Thread(target=produce, name="f1-replay-source", daemon=True)
    thread.start()

    while True:
        item = queue.get()
        if isinstance(item, _Done):
            break
        yield item
    thread.join()
    if error:
        raise error[0]
