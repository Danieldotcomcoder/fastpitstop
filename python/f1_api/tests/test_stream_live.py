"""The live WebSocket must not outlive its client, and must not re-read the
whole session on every poll.

Both of these went wrong at once in production. `_stream_live` polled with
`while True` and never received, so `WebSocketDisconnect` could not be
raised: every browser tab that had ever opened the dashboard left a loop
running for the life of the process. Twenty-four accumulated, each
re-reading and re-validating all 33k events of the race once a second, and
pinned the API at 93% CPU — `/healthz` took 30 seconds, `/sessions` 40, and
the dashboard looked dead.

Nothing tested concurrent viewers, which is exactly why it shipped.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Collection, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import f1_api.stream.websocket as websocket_module
from f1_api.config import Settings

# Reaching for the private loop deliberately: it is the unit whose lifetime
# and read pattern this file exists to pin, and driving it through the route
# would need a live-provenance session a DB-free test cannot resolve.
from f1_api.stream.websocket import _stream_live  # pyright: ignore[reportPrivateUsage]
from f1_domain import DomainEvent, LapCompleted, SessionKey, SessionType
from f1_why import MemoryExplanationStore, MockNarrationModel, ModelNarration

KEY = SessionKey(year=2026, round=14, session_type=SessionType.RACE)
T0 = datetime(2026, 9, 13, 13, 30, tzinfo=UTC)


def _lap(seq: int) -> DomainEvent:
    return LapCompleted(
        session_key=KEY,
        ts_utc=T0 + timedelta(seconds=seq),
        source_seq=seq,
        driver="1",
        lap_number=seq,
    )


class FakeCursorSource:
    """A `StoreSource` stand-in that records how it was asked for events."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []
        self.cursors: list[tuple[int, int] | None] = []
        self.rows_served = 0

    def append(self, event: DomainEvent) -> None:
        self._events.append(event)

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        yield from self._events

    def events_after(
        self,
        session_key: SessionKey,
        after: tuple[int, int] | None,
        *,
        kinds: Collection[str] | None = None,
    ) -> Iterator[tuple[DomainEvent, tuple[int, int]]]:
        self.cursors.append(after)
        start = 0 if after is None else after[0] + 1
        for event in self._events:
            seq = event.source_seq
            if seq < start:
                continue
            self.rows_served += 1
            yield event, (seq, 0)


class FakeWebSocket:
    """Enough of a WebSocket to drive the loop: `receive` hangs until the
    test says the client left, which is exactly how a real connection
    behaves while a viewer sits watching."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._gone = asyncio.Event()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive(self) -> dict[str, Any]:
        await self._gone.wait()
        raise RuntimeError("client disconnected")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    def disconnect(self) -> None:
        self._gone.set()


def _run(websocket: FakeWebSocket, source: FakeCursorSource) -> Any:
    return _stream_live(
        websocket,  # type: ignore[arg-type]
        source,  # type: ignore[arg-type]
        KEY,
        settings=Settings(),
        event_name="Spanish Grand Prix",
        kinds=None,
        radio_store=None,
        narration_model=MockNarrationModel(
            ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True)
        ),
        explanation_store=MemoryExplanationStore(),
        radio_index=None,
    )


def test_the_loop_stops_when_the_client_disconnects() -> None:
    """The regression that pinned production at 93% CPU."""
    asyncio.run(_test_the_loop_stops_when_the_client_disconnects())


async def _test_the_loop_stops_when_the_client_disconnects() -> None:
    websocket, source = FakeWebSocket(), FakeCursorSource()
    source.append(_lap(1))
    task = asyncio.create_task(_run(websocket, source))

    await asyncio.sleep(0.3)
    assert websocket.sent, "the viewer should be receiving events"

    websocket.disconnect()
    # A leaked loop never finishes. Anything more than a poll interval here
    # means the tab is still costing the server work after it closed.
    await asyncio.wait_for(task, timeout=3.0)
    assert websocket.closed


def test_each_poll_asks_only_for_what_is_new() -> None:
    """The other half: a full re-read per poll is what made each leaked
    loop expensive enough to matter."""
    asyncio.run(_test_each_poll_asks_only_for_what_is_new())


async def _test_each_poll_asks_only_for_what_is_new() -> None:
    websocket, source = FakeWebSocket(), FakeCursorSource()
    source.append(_lap(1))
    task = asyncio.create_task(_run(websocket, source))
    await asyncio.sleep(0.3)

    source.append(_lap(2))
    await asyncio.sleep(0.8)
    websocket.disconnect()
    await asyncio.wait_for(task, timeout=3.0)

    # Two events exist and two were served: the second poll did not re-read
    # the first event. A full re-reader would have served 3 or more.
    assert source.rows_served == 2, f"re-read the session: {source.rows_served} rows served"
    assert source.cursors[0] is None
    assert any(cursor == (1, 0) for cursor in source.cursors[1:]), source.cursors


def test_many_viewers_do_not_multiply_the_read() -> None:
    """Ten viewers on one session must each read only what is new — the
    concurrency case nothing exercised before production did."""
    asyncio.run(_test_many_viewers_do_not_multiply_the_read())


async def _test_many_viewers_do_not_multiply_the_read() -> None:
    source = FakeCursorSource()
    source.append(_lap(1))
    sockets = [FakeWebSocket() for _ in range(10)]
    tasks = [asyncio.create_task(_run(ws, source)) for ws in sockets]
    await asyncio.sleep(0.4)

    for ws in sockets:
        ws.disconnect()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)

    # One row each, not one row per poll per viewer.
    assert source.rows_served == len(sockets)
    assert all(ws.closed for ws in sockets)


def test_a_slow_lap_analysis_does_not_hold_up_the_timing(monkeypatch: Any) -> None:
    """The kernel recompute (up to a second a lap) and the narration call
    (a network round trip) used to run inline on the event loop, so while
    either ran nothing else on the server moved. They now run on the
    stream's own thread; timing keeps flowing past a lap that is still
    being analysed."""
    release = threading.Event()

    class SlowFeed:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def feed(self, event: DomainEvent) -> None:
            release.wait(timeout=10)

    monkeypatch.setattr(websocket_module, "LapSnapshotFeed", SlowFeed)
    try:
        asyncio.run(_test_a_slow_lap_analysis_does_not_hold_up_the_timing())
    finally:
        release.set()


async def _test_a_slow_lap_analysis_does_not_hold_up_the_timing() -> None:
    started = time.monotonic()
    websocket, source = FakeWebSocket(), FakeCursorSource()
    source.append(_lap(1))
    task = asyncio.create_task(_run(websocket, source))
    await asyncio.sleep(0.3)

    source.append(_lap(2))
    await asyncio.sleep(0.8)
    sent_laps = [m["payload"].get("lap_number") for m in websocket.sent]
    assert sent_laps == [1, 2]
    # An analysis blocking the loop shows up as time, not as a missing lap:
    # the loop freezes, and every await in this test waits with it.
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"the event loop was blocked for {elapsed:.1f}s by one lap's analysis"

    websocket.disconnect()
    await asyncio.wait_for(task, timeout=3.0)
