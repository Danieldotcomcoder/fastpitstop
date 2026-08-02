"""The one pipeline both live and replay feed, plus the historical path.

`run(records, sink, session_key)` is the single normalization path: an
iterable of `RawRecord`s in, `DomainEvent`s written to the sink out. It does
not know or care whether the records came from a live socket or a replayed
recording — that is the whole Phase 1 invariant (CLAUDE.md), and it is why
weekday replay development is faithful to live.

Three shapes over one core:
- `run(...)` — pull: hand it any iterable of records (replay, or a live
  socket bridged to an iterator).
- `Pipeline` — push: for the live socket, feed records one at a time as they
  arrive (`process`), `seed` from subscribe snapshots, `finalize` at
  shutdown. `run` is just this class driven in a loop.
- `run_events(...)` — the historical path: `f1_historical_adapter` already
  emits `DomainEvent`s (no raw records, no state machine), so they go
  straight to the sink. No materialized `SessionState` is written (history
  has no live state projection); the event log + typed tables are.

The transform is pure: no wall clock, no randomness, no I/O reach an event.
`wall_seconds` in the metrics is measured strictly around the run.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from itertools import islice
from typing import cast

from pydantic import JsonValue

from f1_domain import DomainEvent, SessionKey, SessionState
from f1_parse import SessionStateMachine
from f1_pipeline.metrics import MetricsCollector, RunMetrics
from f1_recording import RawRecord
from f1_store import EventSink


def _sink_rows(sink: EventSink) -> Mapping[str, int]:
    """Rows the sink recorded as written this run, if it tracks them
    (`PostgresSink`/`MemorySink` do; `NullSink` doesn't)."""
    written = getattr(sink, "written_rows", None)
    return cast("Mapping[str, int]", written) if isinstance(written, Mapping) else {}


class Pipeline:
    """Stateful push-driver around one `SessionStateMachine` and one sink.

    One instance per session (one recording / one live connection). Feed it
    records with `process`, seed it from subscribe snapshots with `seed`, and
    call `finalize` once at the end to write the materialized state and flush.
    """

    def __init__(
        self,
        session_key: SessionKey,
        sink: EventSink,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._machine = SessionStateMachine(session_key)
        self._sink = sink
        self._collector = MetricsCollector()
        self._clock = clock
        self._start = clock()

    def seed(self, subscribe_snapshot: JsonValue) -> None:
        """Adopt a subscribe snapshot as the baseline (silent — no events).
        Used at live connect/reconnect; replay carries the snapshot as a
        `_meta.subscribe_snapshot` record instead, so `process` handles it."""
        self._machine.seed(subscribe_snapshot)

    def process(self, record: RawRecord) -> list[DomainEvent]:
        """Normalize one record, write any events it produced, return them."""
        self._collector.record_input()
        events = self._machine.apply(record)
        if events:
            self._sink.write_events(events)
            self._collector.observe(events)
        return events

    @property
    def state(self) -> SessionState:
        return self._machine.state

    def finalize(self) -> RunMetrics:
        """Write the materialized current-state, flush, and return metrics."""
        self._sink.write_state(self._machine.state)
        self._sink.flush()
        wall = self._clock() - self._start
        return self._collector.finish(_sink_rows(self._sink), wall)


def run(
    records: Iterable[RawRecord],
    sink: EventSink,
    *,
    session_key: SessionKey,
    clock: Callable[[], float] = time.monotonic,
) -> RunMetrics:
    """Drive `records` through the pipeline into `sink`. The single path both
    live and replay use — it never learns which one it is."""
    pipeline = Pipeline(session_key, sink, clock=clock)
    for record in records:
        pipeline.process(record)
    return pipeline.finalize()


def run_events(
    events: Iterable[DomainEvent],
    sink: EventSink,
    *,
    chunk_size: int = 1000,
    clock: Callable[[], float] = time.monotonic,
) -> RunMetrics:
    """Write an already-normalized event stream (the historical adapter's
    output) straight to the sink. No state machine, no materialized state —
    just the event log + typed tables. Streams in chunks so a whole season's
    events never need to sit in memory at once."""
    collector = MetricsCollector()
    start = clock()
    for chunk in _chunked(events, chunk_size):
        sink.write_events(chunk)
        collector.observe(chunk)
    sink.flush()
    wall = clock() - start
    return collector.finish(_sink_rows(sink), wall)


def _chunked(events: Iterable[DomainEvent], size: int) -> Iterator[list[DomainEvent]]:
    iterator = iter(events)
    while chunk := list(islice(iterator, size)):
        yield chunk
