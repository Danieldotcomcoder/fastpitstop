"""The one pipeline both live and replay feed, plus the historical path.

`run(records, sink, session_key)` is the single normalization path: an
iterable of `RawRecord`s in, `DomainEvent`s written to the sink out. It does
not know or care whether the records came from a live socket or a replayed
recording — that is the whole Phase 1 invariant (CLAUDE.md), and it is why
weekday replay development is faithful to live.

Four shapes over one core:
- `run(...)` — pull: hand it any iterable of records (replay, or a live
  socket bridged to an iterator).
- `Pipeline` — push: for the live socket, feed records one at a time as they
  arrive (`process`), `seed` from subscribe snapshots, `finalize` at
  shutdown. `run` is just this class driven in a loop.
- `SessionRouter` / `run_routed(...)` — one continuous record stream that
  spans SEVERAL sessions (a recorder left running for a whole weekend),
  split into one `Pipeline` per session by the stream's own `SessionInfo`.
  The live socket and a whole-weekend replay both go through it.
- `run_events(...)` — the historical path: `f1_historical_adapter` already
  emits `DomainEvent`s (no raw records, no state machine), so they go
  straight to the sink. No materialized `SessionState` is written (history
  has no live state projection); the event log + typed tables are.

The transform is pure: no wall clock, no randomness, no I/O reach an event.
`wall_seconds` in the metrics is measured strictly around the run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from itertools import islice
from typing import Any, cast

from pydantic import JsonValue

from f1_domain import DomainEvent, SessionKey, SessionState, SessionStatusChanged
from f1_parse import SessionStateMachine, deep_merge
from f1_pipeline.metrics import MetricsCollector, RunMetrics
from f1_pipeline.session_identity import (
    SESSION_INFO_TOPIC,
    SessionIdentityError,
    session_key_from_snapshot,
)
from f1_recording import RawRecord
from f1_store import EventSink

logger = logging.getLogger(__name__)

SUBSCRIBE_SNAPSHOT_TOPIC = "_meta.subscribe_snapshot"


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
        self._session_key = session_key
        self._machine = SessionStateMachine(session_key)
        self._sink = sink
        self._collector = MetricsCollector()
        self._clock = clock
        self._start = clock()

    def seed(self, subscribe_snapshot: JsonValue) -> None:
        """Adopt a subscribe snapshot as the baseline. Writes nothing itself;
        on the FIRST seed the state machine queues a one-off position + roster
        baseline that the next `process` call emits (see
        `SessionStateMachine.seed`). Used at live connect/reconnect; replay
        carries the snapshot as a `_meta.subscribe_snapshot` record instead,
        so `process` handles it — both routes reach the same `seed`."""
        self._machine.seed(subscribe_snapshot)

    def process(self, record: RawRecord) -> list[DomainEvent]:
        """Normalize one record, write any events it produced, return them.

        A session-status change also rewrites the materialized `sessions`
        row straight away. Whether a session is live is read off that row
        (`f1_store.live_session_keys`), and a live ingest runs for days: if
        the row were only written at shutdown, a status from a previous
        session would decide whether this one counts as running."""
        self._collector.record_input()
        events = self._machine.apply(record)
        if events:
            self._sink.write_events(events)
            self._collector.observe(events)
            if any(isinstance(event, SessionStatusChanged) for event in events):
                self._sink.write_state(self._machine.state)
        return events

    @property
    def session_key(self) -> SessionKey:
        return self._session_key

    @property
    def state(self) -> SessionState:
        return self._machine.state

    def finalize(self) -> RunMetrics:
        """Write the materialized current-state, flush, and return metrics."""
        self._sink.write_state(self._machine.state)
        self._sink.flush()
        wall = self._clock() - self._start
        return self._collector.finish(_sink_rows(self._sink), wall)


class SessionRouter:
    """Routes one continuous record stream to one `Pipeline` PER SESSION.

    The live recorder runs for a whole race weekend on one connection, and
    F1 does not reconnect it between sessions: it announces the next one
    over the open socket with a complete `SessionInfo` keyframe (a new
    `Key`, `Name` and `Path`), followed within seconds by fresh keyframes
    for every other topic (roster, timing, status series, lap count).
    Keying the whole connection off its first snapshot filed the 2026
    Azerbaijan FP3, qualifying and race under "practice_2", with the
    practice session's final status still on the row, so the race never
    read as live. A state machine also cannot be carried across a session
    boundary: the status series restarts its indices, so the old machine
    silently skipped the race's "Started".

    So identity is re-derived from every `SessionInfo` the stream carries,
    in a subscribe snapshot or as a feed message, and a different key
    finalizes the current session and starts a fresh `Pipeline` for the new
    one. The fresh machine is not seeded: the keyframe burst that follows
    the announcement is the new session's full state, and applying it as
    ordinary records emits the roster, positions, venue and status just as
    a live session produces them. A snapshot of the SAME session (a
    reconnect) re-seeds the current pipeline, as before.

    `year` / `round_number` / `session_type` override the feed's own values
    exactly as `session_key_from_snapshot` does; passing all three pins the
    key and turns routing off.
    """

    def __init__(
        self,
        sink: EventSink,
        *,
        year: int | None = None,
        round_number: int | None = None,
        session_type: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._year = year
        self._round = round_number
        self._session_type = session_type
        self._clock = clock
        self._pipeline: Pipeline | None = None
        self._info: dict[str, Any] | None = None
        self._finished: dict[SessionKey, RunMetrics] = {}

    @property
    def session_key(self) -> SessionKey | None:
        """The session records are currently being filed under."""
        return self._pipeline.session_key if self._pipeline is not None else None

    @property
    def pipeline(self) -> Pipeline | None:
        return self._pipeline

    def seed(self, subscribe_snapshot: JsonValue) -> None:
        """A (re)connect's full-state snapshot. Raises `SessionIdentityError`
        only when no session has been identified yet and this snapshot does
        not identify one either — there is nothing to file records under."""
        if isinstance(subscribe_snapshot, dict):
            info = subscribe_snapshot.get(SESSION_INFO_TOPIC)
            if isinstance(info, dict):
                self._info = dict(info)
        key = self._identify(subscribe_snapshot, strict=self._pipeline is None)
        if key is not None and key != self.session_key:
            self._switch(key)
        if self._pipeline is not None:
            self._pipeline.seed(subscribe_snapshot)

    def process(self, record: RawRecord) -> list[DomainEvent]:
        """Route one record to its session's pipeline; returns its events.

        A record that arrives before any session is identified is dropped
        rather than guessed at: the recorder refuses feed messages before a
        snapshot, so on the live path this cannot happen."""
        if record.topic == SUBSCRIBE_SNAPSHOT_TOPIC:
            self.seed(record.payload)
            return []
        if record.topic == SESSION_INFO_TOPIC and isinstance(record.payload, dict):
            self._observe_session_info(record.payload)
        if self._pipeline is None:
            return []
        return self._pipeline.process(record)

    def finalize(self) -> dict[SessionKey, RunMetrics]:
        """Finish the current session too; metrics for every session seen,
        in the order they ended."""
        if self._pipeline is not None:
            self._finished[self._pipeline.session_key] = self._pipeline.finalize()
            self._pipeline = None
        return dict(self._finished)

    def _observe_session_info(self, payload: dict[str, Any]) -> None:
        # A keyframe replaces the topic outright (it is how F1 announces a
        # new session); anything else is a delta onto what we know.
        if payload.get("_kf") or self._info is None:
            merged: Any = dict(payload)
        else:
            merged = deep_merge(self._info, payload)
        if isinstance(merged, dict):
            self._info = cast("dict[str, Any]", merged)
        key = self._identify({SESSION_INFO_TOPIC: self._info}, strict=False)
        if key is not None and key != self.session_key:
            self._switch(key)
            # The keyframe burst about to arrive IS this session's baseline,
            # so adopt an empty one now: a reconnect later in the session is
            # then the silent re-seed it would be for any other session,
            # rather than a first seed re-publishing the whole order mid-race.
            if self._pipeline is not None:
                self._pipeline.seed({})

    def _identify(self, snapshot: Any, *, strict: bool) -> SessionKey | None:
        try:
            return session_key_from_snapshot(
                snapshot,
                year=self._year,
                round_number=self._round,
                session_type=self._session_type,
            )
        except SessionIdentityError:
            if strict:
                raise
            return None

    def _switch(self, key: SessionKey) -> None:
        previous = self._pipeline
        if previous is not None:
            logger.info("Session changed: %s -> %s", previous.session_key, key)
            self._finished[previous.session_key] = previous.finalize()
        logger.info("Ingesting %s", key)
        self._pipeline = Pipeline(key, self._sink, clock=self._clock)


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


def run_routed(
    records: Iterable[RawRecord],
    sink: EventSink,
    *,
    year: int | None = None,
    round_number: int | None = None,
    session_type: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[SessionKey, RunMetrics]:
    """`run` for a stream that may span several sessions: each record is
    filed under the session the stream itself says is running (see
    `SessionRouter`). The same routing the live socket uses, so a replayed
    weekend recording lands exactly where the live ingest would have put
    it."""
    router = SessionRouter(
        sink, year=year, round_number=round_number, session_type=session_type, clock=clock
    )
    for record in records:
        router.process(record)
    return router.finalize()


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
