"""The live WebSocket: one shared hub per session, any number of viewers.

What went wrong before, each pinned below:

- `_stream_live` polled with `while True` and never received, so a closed
  tab never ended its loop. Twenty-four accumulated between two deploys,
  each re-reading the whole race once a second, and pinned the API at 93%
  CPU — `/healthz` took 30 seconds and the dashboard looked dead.
- Every viewer then ran the whole live path for itself: its own store
  connection, its own kernel (one replay's kernel measured past 400 MB, the
  API is capped at 2 GB), its own narration calls. Production Postgres
  allows 25 connections, so a handful of viewers during a race would have
  exhausted it. A viewer joining at lap 40 waited on forty kernel
  recomputes before seeing a snapshot.
- The kernel recompute and the narration call ran inline on the event
  loop, so while either ran nothing else on the server moved.

Nothing tested concurrent viewers, which is exactly why it shipped.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Collection, Container, Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import f1_api.stream.live as live_module
from f1_api.config import Settings
from f1_api.stream.live import LiveDeps, LiveHub, LiveHubs

# Reaching for the private viewer loop deliberately: it is the unit whose
# lifetime this file pins, and driving it through the route would need a
# live-provenance session a DB-free test cannot resolve.
from f1_api.stream.websocket import _stream_live  # pyright: ignore[reportPrivateUsage]
from f1_domain import DomainEvent, LapCompleted, SessionKey, SessionType
from f1_kernel import EventSource
from f1_why import MemoryExplanationStore, MockNarrationModel, ModelNarration

KEY = SessionKey(year=2026, round=16, session_type=SessionType.RACE)
T0 = datetime(2026, 10, 4, 7, 0, tzinfo=UTC)


def _lap(seq: int) -> DomainEvent:
    return LapCompleted(
        session_key=KEY,
        ts_utc=T0 + timedelta(seconds=seq),
        source_seq=seq,
        driver="1",
        lap_number=seq,
    )


class FakeCursorSource:
    """A `StoreSource` stand-in that follows by time exactly as the real one
    does (`f1_store.replay_events_since`), and records how it was asked."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []
        self.since: list[datetime | None] = []
        self.rows_served = 0
        self.fail_next = 0

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

    def events_since(
        self,
        session_key: SessionKey,
        since: datetime | None,
        *,
        kinds: Collection[str] | None = None,
        seen: Container[tuple[int, int, datetime]] = (),
    ) -> Iterator[tuple[DomainEvent, tuple[int, int, datetime]]]:
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("server closed the connection unexpectedly")
        self.since.append(since)
        ordered = sorted(self._events, key=lambda e: (e.ts_utc, e.source_seq))
        for event in ordered:
            if since is not None and event.ts_utc < since:
                continue
            identity = (event.source_seq, 0, event.ts_utc)
            if identity in seen:
                continue
            self.rows_served += 1
            yield event, identity


class FakeWebSocket:
    """Enough of a WebSocket to drive the loop: `receive` hangs until the
    test says the client left, which is exactly how a real connection
    behaves while a viewer sits watching."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._gone = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive(self) -> dict[str, Any]:
        await self._gone.wait()
        raise RuntimeError("client disconnected")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    def disconnect(self) -> None:
        self._gone.set()

    def laps(self) -> list[Any]:
        return [m["payload"].get("lap_number") for m in self.sent if m["kind"] == "lap_completed"]


def _hub(source: FakeCursorSource) -> LiveHub:
    @contextmanager
    def open_source() -> Generator[EventSource | None, None, None]:
        yield source  # pyright: ignore[reportReturnType]

    @contextmanager
    def open_deps() -> Generator[LiveDeps, None, None]:
        model = MockNarrationModel(
            ModelNarration(text="", cited_evidence_ids=(), no_clear_cause=True)
        )
        yield None, model, MemoryExplanationStore(), None

    return LiveHub(
        KEY,
        settings=Settings(),
        event_name="Bahrain Grand Prix",
        kinds=None,
        open_source=open_source,
        open_deps=open_deps,
        poll_interval_s=0.05,
    )


def _watch(websocket: FakeWebSocket, hub: LiveHub) -> Any:
    return _stream_live(websocket, hub, interval_s=0.05)  # type: ignore[arg-type]


def test_the_loop_stops_when_the_client_disconnects() -> None:
    """The regression that pinned production at 93% CPU."""
    asyncio.run(_test_the_loop_stops_when_the_client_disconnects())


async def _test_the_loop_stops_when_the_client_disconnects() -> None:
    source = FakeCursorSource()
    source.append(_lap(1))
    hub = _hub(source)
    websocket = FakeWebSocket()
    task = asyncio.create_task(_watch(websocket, hub))
    try:
        await asyncio.sleep(0.3)
        assert websocket.laps() == [1], "the viewer should be receiving events"

        websocket.disconnect()
        # A leaked loop never finishes. Anything more than an interval here
        # means the tab is still costing the server work after it closed.
        await asyncio.wait_for(task, timeout=3.0)
        assert websocket.closed
    finally:
        hub.stop()


def test_each_poll_asks_only_for_what_is_new() -> None:
    """A full re-read per poll is what made each leaked loop expensive."""
    asyncio.run(_test_each_poll_asks_only_for_what_is_new())


async def _test_each_poll_asks_only_for_what_is_new() -> None:
    source = FakeCursorSource()
    source.append(_lap(1))
    hub = _hub(source)
    try:
        await asyncio.sleep(0.2)
        source.append(_lap(2))
        await asyncio.sleep(0.3)
    finally:
        hub.stop()
    assert source.rows_served == 2, f"re-read the session: {source.rows_served} rows served"
    assert source.since[0] is None, "the first read is the whole session"
    assert all(since is not None for since in source.since[1:]), "later reads are a window"


def test_many_viewers_share_one_read_and_one_kernel() -> None:
    """Ten viewers on one session: the store is read once, not ten times,
    and every viewer still receives everything."""
    asyncio.run(_test_many_viewers_share_one_read_and_one_kernel())


async def _test_many_viewers_share_one_read_and_one_kernel() -> None:
    source = FakeCursorSource()
    source.append(_lap(1))
    hubs = LiveHubs(grace_s=0)
    hub = hubs.acquire(KEY, None, lambda: _hub(source))
    others = [hubs.acquire(KEY, None, lambda: _hub(source)) for _ in range(9)]
    assert all(other is hub for other in others), "one session, one hub"

    sockets = [FakeWebSocket() for _ in range(10)]
    tasks = [asyncio.create_task(_watch(ws, hub)) for ws in sockets]
    await asyncio.sleep(0.3)
    source.append(_lap(2))
    await asyncio.sleep(0.3)
    for ws in sockets:
        ws.disconnect()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)
    for _ in range(10):
        hubs.release(hub)

    assert source.rows_served == 2, "each row read once for all ten viewers"
    assert all(ws.laps() == [1, 2] for ws in sockets)
    assert all(ws.closed for ws in sockets)
    assert hub.stopped and hubs.active() == 0, "the last viewer out retires the hub"


def test_a_viewer_joining_mid_race_gets_the_race_so_far() -> None:
    """Open the page at any point of the race and see where it stands."""
    asyncio.run(_test_a_viewer_joining_mid_race_gets_the_race_so_far())


async def _test_a_viewer_joining_mid_race_gets_the_race_so_far() -> None:
    source = FakeCursorSource()
    for seq in range(1, 31):
        source.append(_lap(seq))
    hub = _hub(source)
    try:
        await asyncio.sleep(0.3)  # the race is already running
        late = FakeWebSocket()
        task = asyncio.create_task(_watch(late, hub))
        await asyncio.sleep(0.3)
        assert late.laps() == list(range(1, 31))
        source.append(_lap(31))
        await asyncio.sleep(0.3)
        assert late.laps()[-1] == 31
        late.disconnect()
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        hub.stop()


def test_a_slow_lap_analysis_does_not_hold_up_the_timing(monkeypatch: Any) -> None:
    """The kernel recompute (up to a second a lap) and the narration call (a
    network round trip) run on the analysis thread; timing keeps flowing
    past a lap that is still being analysed."""
    release = threading.Event()

    class SlowFeed:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def feed(self, event: DomainEvent) -> None:
            release.wait(timeout=10)

    monkeypatch.setattr(live_module, "LapSnapshotFeed", SlowFeed)
    try:
        asyncio.run(_test_a_slow_lap_analysis_does_not_hold_up_the_timing())
    finally:
        release.set()


async def _test_a_slow_lap_analysis_does_not_hold_up_the_timing() -> None:
    started = time.monotonic()
    source = FakeCursorSource()
    source.append(_lap(1))
    hub = _hub(source)
    websocket = FakeWebSocket()
    task = asyncio.create_task(_watch(websocket, hub))
    try:
        await asyncio.sleep(0.3)
        source.append(_lap(2))
        await asyncio.sleep(0.5)
        assert websocket.laps() == [1, 2]
        # An analysis blocking the loop shows up as time, not as a missing
        # lap: the loop freezes, and every await in this test waits with it.
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"blocked for {elapsed:.1f}s by one lap's analysis"
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        hub.stop()


def test_a_dropped_store_is_reopened_without_losing_or_repeating_events(
    monkeypatch: Any,
) -> None:
    """Postgres restarting mid-race must not end the race for everyone."""
    monkeypatch.setattr(live_module, "RECONNECT_WAIT_S", 0.05)
    asyncio.run(_test_a_dropped_store_is_reopened())


async def _test_a_dropped_store_is_reopened() -> None:
    source = FakeCursorSource()
    source.append(_lap(1))
    hub = _hub(source)
    websocket = FakeWebSocket()
    task = asyncio.create_task(_watch(websocket, hub))
    try:
        await asyncio.sleep(0.3)
        source.fail_next = 2
        source.append(_lap(2))
        await asyncio.sleep(0.6)
        source.append(_lap(3))
        await asyncio.sleep(0.4)
        assert websocket.laps() == [1, 2, 3]
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        hub.stop()


def test_a_returning_viewer_finds_the_hub_still_running() -> None:
    """A phone switching networks reconnects within seconds; it must find
    the session's analysis warm, not start it again."""
    source = FakeCursorSource()
    hubs = LiveHubs(grace_s=30)
    hub = hubs.acquire(KEY, None, lambda: _hub(source))
    try:
        hubs.release(hub)
        assert not hub.stopped
        again = hubs.acquire(KEY, None, lambda: _hub(source))
        assert again is hub
        hubs.release(again)
    finally:
        hub.stop()


def test_a_late_joiner_gets_old_gap_history_compacted_and_nothing_else_lost() -> None:
    """Gap updates are most of a race. A viewer joining at lap 40 receives,
    per driver, the last update from before the history window and every
    update inside it (exactly the tower's state for any delay the slider
    allows), and every other event in full. A full download took long
    enough on a slow phone for the keepalive to drop the viewer, who then
    reconnected and started again, forever."""
    from f1_api.stream.live import JOIN_GAP_HISTORY_S
    from f1_domain import GapUpdated

    now = datetime.now(UTC)
    old = now - timedelta(seconds=JOIN_GAP_HISTORY_S + 600)
    recent = now - timedelta(seconds=30)

    def gap(seq: int, driver: str, at: datetime, seconds: float) -> DomainEvent:
        return GapUpdated(
            session_key=KEY,
            ts_utc=at + timedelta(seconds=seconds),
            source_seq=seq,
            driver=driver,
            gap_to_leader=timedelta(seconds=seconds),
        )

    source = FakeCursorSource()
    seq = 0
    for i in range(200):  # an old stretch of race: 200 updates each
        for driver in ("1", "63"):
            seq += 1
            source.append(gap(seq, driver, old, 1.0 + i))
    seq += 1
    source.append(_lap(seq))  # not a gap: always kept
    for i in range(3):  # inside the window
        seq += 1
        source.append(gap(seq, "1", recent, 0.5 + i))

    hub = _hub(source)
    try:
        deadline = time.monotonic() + 5
        while len(hub.since(0)) < seq and time.monotonic() < deadline:
            time.sleep(0.05)
        first, tail_from = hub.join(now=now)
    finally:
        hub.stop()

    assert tail_from == seq, "the tail starts after everything published"
    kinds = [json.loads(text)["kind"] for text in first]
    assert kinds.count("lap_completed") == 1
    gaps = [
        json.loads(text)["payload"] for text in first if json.loads(text)["kind"] == "gap_updated"
    ]
    # One old update per driver (its last), plus all three recent ones.
    assert len(gaps) == 2 + 3
    last_old = {g["driver"]: g["gap_to_leader"] for g in gaps[:2]}
    assert last_old == {"1": "PT3M20S", "63": "PT3M20S"}, last_old


def test_a_worker_restart_does_not_freeze_the_live_stream() -> None:
    """The 2026 Bahrain GP qualifying regression: the recorder restarted,
    its sequence numbers started again from zero, and a hub following by
    sequence never delivered another event. Following by time, the events
    after the restart arrive like any others."""
    asyncio.run(_test_a_worker_restart_does_not_freeze_the_live_stream())


async def _test_a_worker_restart_does_not_freeze_the_live_stream() -> None:
    source = FakeCursorSource()
    for seq in (25_001, 25_002):  # the recorder had been running for hours
        source.append(_lap(seq))
    hub = _hub(source)
    websocket = FakeWebSocket()
    task = asyncio.create_task(_watch(websocket, hub))
    try:
        await asyncio.sleep(0.3)
        # The worker restarts: sequence numbers begin again at 0, while the
        # events themselves are, of course, later in time.
        for seq in (0, 1, 2):
            source.append(
                LapCompleted(
                    session_key=KEY,
                    ts_utc=T0 + timedelta(seconds=25_010 + seq),
                    source_seq=seq,
                    driver="1",
                    lap_number=100 + seq,
                )
            )
        await asyncio.sleep(0.4)
        assert websocket.laps() == [25_001, 25_002, 100, 101, 102]
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        hub.stop()


def test_an_event_stamped_slightly_in_the_past_arrives_once() -> None:
    """Some events carry the feed's own timestamp, a few seconds earlier
    than events already stored. The hub re-reads a window behind its newest
    event so they still arrive, and never sends a re-read event twice."""
    asyncio.run(_test_an_event_stamped_slightly_in_the_past_arrives_once())


async def _test_an_event_stamped_slightly_in_the_past_arrives_once() -> None:
    source = FakeCursorSource()
    source.append(_lap(10))
    hub = _hub(source)
    websocket = FakeWebSocket()
    task = asyncio.create_task(_watch(websocket, hub))
    try:
        await asyncio.sleep(0.3)
        source.append(_lap(5))  # stored after lap 10, stamped 5 s earlier
        await asyncio.sleep(0.4)
        assert sorted(websocket.laps()) == [5, 10], websocket.laps()
        assert source.rows_served == 2, "the overlap re-read sent nothing twice"
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=3.0)
    finally:
        hub.stop()
