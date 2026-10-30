"""Replays shared between viewers: one build, one kernel pass and one
explanation batch per session, however many people watch it, each viewer
at its own pace. See `f1_api.stream.replay_hub`.

What went wrong before: every replay viewer loaded and analysed the whole
race for itself, 100-280 MB each, in one process. A load test on 2025
Suzuka (2026-10-24) had 8 viewers at 1.3 GB with pages taking 20 s, and 32
viewers receiving nothing at all. Production caps the API at 3 GB.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import multiprocessing
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from api_fixture import HISTORICAL_ROOT, SESSION_KEY
from fastapi.testclient import TestClient

import f1_api.stream.replay as replay_module
import f1_api.stream.replay_hub as replay_hub_module
from f1_api.config import Settings
from f1_api.schemas import StreamEnvelope
from f1_api.stream.replay import (
    ReplayCursor,
    ReplayPace,
    ReplayTimeline,
    envelope_tail,
    kernel_outputs,
    replay_events,
    stamped_text,
    utc_stamp,
)
from f1_api.stream.replay_hub import REPLAY_HUBS, ReplayHub, ReplayHubs, ReplayLimits
from f1_api.stream.replay_worker import WorkerJob, run_job, run_worker

# The private viewer loop and replay spec are the units under test here:
# driving them through the route needs a real socket per viewer.
from f1_api.stream.websocket import (
    _ReplaySpec,  # pyright: ignore[reportPrivateUsage]
    _stream_replay,  # pyright: ignore[reportPrivateUsage]
)
from f1_domain import SessionKey
from f1_kernel import HistoricalSource

KEY = SessionKey.model_validate({"year": 2024, "round": 1, "session_type": "race"})
WALL = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
OUTPUT_KINDS = {"kernel_snapshot", "call"}


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeWebSocket:
    """Enough of a WebSocket to drive the viewer loop: `receive` hangs until
    the test says the client left."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._gone = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive(self) -> dict[str, Any]:
        await self._gone.wait()
        return {"type": "websocket.disconnect"}

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    def disconnect(self) -> None:
        self._gone.set()


def _watch(websocket: FakeWebSocket, hub: ReplayHub) -> Any:
    return _stream_replay(websocket, hub, speed=0)  # type: ignore[arg-type]


def _settings() -> Settings:
    return Settings(historical_root=HISTORICAL_ROOT)


def _spec(settings: Settings) -> _ReplaySpec:
    return _ReplaySpec(
        settings,
        KEY,
        event_name=None,
        circuit_name=None,
        kinds=replay_module.DEFAULT_STREAM_KINDS,
        anchor_to_first_lap=True,
    )


def _timeline(**kwargs: Any) -> ReplayTimeline:
    return ReplayTimeline.build(HistoricalSource(HISTORICAL_ROOT), KEY, **kwargs)


def _walk(
    timeline: ReplayTimeline, speed: float, *, wait_for_analysis: bool = True
) -> list[dict[str, Any]]:
    """One viewer's whole replay over `timeline`, on a fake clock."""
    clock = _FakeClock()
    cursor = ReplayCursor(timeline, ReplayPace(speed), clock=clock.now, wall_clock=lambda: WALL)
    sent: list[dict[str, Any]] = []
    while True:
        due = cursor.due()
        sent.extend(json.loads(text) for text in due.texts)
        if due.wait_s is None:
            return sent
        if due.analysing:
            assert wait_for_analysis, "the walk reached the end before the analysis"
            timeline.wait_analysed(5.0)
        else:
            clock.sleep(due.wait_s)


def _wall(message: dict[str, Any]) -> float:
    return (datetime.fromisoformat(message["event_time_utc"]) - WALL).total_seconds()


def _without_stamps(messages: Sequence[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(m["kind"], m["payload"]) for m in messages]


@pytest.mark.parametrize(
    "ts",
    [
        datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 1, 12, 0, 0, 123456, tzinfo=UTC),
        datetime(2026, 6, 1, 12, 0, 0, 1000, tzinfo=UTC),
    ],
)
def test_a_shared_envelope_restamped_is_byte_for_byte_what_pydantic_writes(ts: datetime) -> None:
    payload = {"driver": "44", "gap": 1.25, "name": "Zhou"}
    placeholder = StreamEnvelope(
        event_time_utc=datetime(2000, 1, 1, tzinfo=UTC), kind="gap_updated", payload=payload
    )
    direct = StreamEnvelope(event_time_utc=ts, kind="gap_updated", payload=payload)

    assert stamped_text(utc_stamp(ts), envelope_tail(placeholder)) == direct.model_dump_json()


def test_viewers_at_different_speeds_get_the_same_race_on_their_own_clocks() -> None:
    timeline = _timeline()
    timeline.start_analysis()
    assert timeline.wait_analysed(30.0)
    try:
        slow = _walk(timeline, 1.0)
        fast = _walk(timeline, 16.0)
    finally:
        timeline.close()

    assert _without_stamps(slow) == _without_stamps(fast), "one race, whoever watches it"
    assert any(m["kind"] == "kernel_snapshot" for m in slow)
    laps_slow = [_wall(m) for m in slow if m["kind"] == "lap_completed"]
    laps_fast = [_wall(m) for m in fast if m["kind"] == "lap_completed"]
    # The same stretch of race, sixteen times quicker on the fast clock.
    assert laps_fast[-1] - laps_fast[0] == pytest.approx((laps_slow[-1] - laps_slow[0]) / 16)
    for sent in (slow, fast):
        walls = [_wall(m) for m in sent]
        assert walls == sorted(walls), "stamps never go backwards"


def test_a_viewer_ahead_of_the_analysis_gets_each_lap_late_never_early() -> None:
    """The analysis is held back until the viewer has seen the whole race:
    every snapshot then goes out after its lap, stamped no earlier."""
    source = HistoricalSource(HISTORICAL_ROOT)
    timeline = ReplayTimeline.build(source, KEY, keep_events=False)
    clock = _FakeClock()
    cursor = ReplayCursor(timeline, ReplayPace(4.0), clock=clock.now, wall_clock=lambda: WALL)
    sent: list[dict[str, Any]] = []
    while True:
        due = cursor.due()
        sent.extend(json.loads(text) for text in due.texts)
        if due.wait_s is None:
            break
        if due.analysing:
            assert not any(m["kind"] in OUTPUT_KINDS for m in sent)
            # The worker's results, arriving only now.
            events = replay_events(source, KEY, replay_module.DEFAULT_STREAM_KINDS)
            for after, tail in kernel_outputs(timeline.spec(), events):
                timeline.add_output(after, tail)
            timeline.finish_analysis()
        else:
            clock.sleep(due.wait_s)

    snapshots = [i for i, m in enumerate(sent) if m["kind"] == "kernel_snapshot"]
    last_event = max(i for i, m in enumerate(sent) if m["kind"] not in OUTPUT_KINDS)
    assert snapshots, "the fixture race streamed no snapshots"
    assert min(snapshots) > last_event, "a snapshot went out before the analysis existed"
    assert all(_wall(sent[i]) >= _wall(sent[last_event]) for i in snapshots)


def _job(timeline: ReplayTimeline, **changes: Any) -> WorkerJob:
    spec = dataclasses.replace(timeline.spec(), **changes)
    return WorkerJob(_settings(), spec, frozenset(replay_module.DEFAULT_STREAM_KINDS), story=True)


def _run_in_process(job: WorkerJob) -> list[tuple[Any, ...]]:
    """`run_job` without the process: what a worker would send."""
    receive, send = multiprocessing.Pipe(duplex=False)
    messages: list[tuple[Any, ...]] = []
    worker = threading.Thread(target=run_job, args=(job, send))
    worker.start()
    while True:
        message = receive.recv()
        messages.append(message)
        if message[0] == "done":
            break
    worker.join(timeout=10.0)
    return messages


def test_a_worker_sends_the_story_first_then_the_kernel_pass_then_done() -> None:
    timeline = _timeline(keep_events=False)
    messages = _run_in_process(_job(timeline))

    assert messages[0][0] == "story" and messages[-1] == ("done",)
    events = replay_events(
        HistoricalSource(HISTORICAL_ROOT), KEY, replay_module.DEFAULT_STREAM_KINDS
    )
    expected = list(kernel_outputs(timeline.spec(), events))
    assert expected, "the fixture race produces no snapshots"
    assert [(m[1], m[2]) for m in messages if m[0] == "output"] == expected


def test_events_that_changed_since_the_build_mean_no_snapshots_not_wrong_ones() -> None:
    timeline = _timeline(keep_events=False)
    messages = _run_in_process(_job(timeline, fingerprint="not the race the timeline holds"))

    assert [m[0] for m in messages] == ["story", "done"]


def test_a_replay_nobody_wants_any_more_stops_its_worker_process() -> None:
    timeline = _timeline(keep_events=False)
    started = time.monotonic()
    run_worker(
        _job(timeline),
        on_story=lambda _bundles: None,
        on_output=lambda _after, _tail: None,
        stopped=lambda: time.monotonic() - started > 0.5,
    )

    assert not [p for p in multiprocessing.active_children() if p.is_alive()]


def test_many_viewers_share_one_build_and_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_many_viewers_share_one_build_and_one_worker(monkeypatch))


async def _test_many_viewers_share_one_build_and_one_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs: list[WorkerJob] = []

    def counting(job: WorkerJob, **kwargs: Any) -> None:
        jobs.append(job)
        run_worker(job, **kwargs)

    monkeypatch.setattr(replay_hub_module, "run_worker", counting)
    spec = _spec(_settings())
    builds = 0

    def make() -> ReplayHub:
        nonlocal builds
        builds += 1
        return spec.make_hub(shared=True)

    hubs = ReplayHubs(grace_s=0)
    acquired = [hubs.acquire(spec.key, make) for _ in range(10)]
    hub = acquired[0]
    assert all(other is hub for other in acquired), "one session, one hub"
    sockets = [FakeWebSocket() for _ in range(10)]
    await asyncio.wait_for(asyncio.gather(*(_watch(ws, hub) for ws in sockets)), timeout=120.0)
    for _ in acquired:
        hubs.release(hub)

    assert builds == 1
    assert len(jobs) == 1, f"{len(jobs)} analyses for one race"
    first = _without_stamps(sockets[0].sent)
    assert any(kind == "kernel_snapshot" for kind, _ in first), "the worker's snapshots arrived"
    assert all(_without_stamps(ws.sent) == first for ws in sockets), "everyone saw the same race"
    assert all(ws.closed for ws in sockets)
    assert hub.stopped and hubs.active() == 0, "the last viewer out retires the hub"


def test_a_viewer_who_stops_waiting_leaves_the_build_to_the_others() -> None:
    asyncio.run(_test_a_viewer_who_stops_waiting_leaves_the_build_to_the_others())


async def _test_a_viewer_who_stops_waiting_leaves_the_build_to_the_others() -> None:
    go = threading.Event()

    def build() -> ReplayTimeline:
        go.wait(10.0)
        return _timeline()

    hub = ReplayHub(KEY, build=build, limits=ReplayLimits(builds=1, analyses=1))
    try:
        impatient = asyncio.ensure_future(hub.timeline())
        await asyncio.sleep(0.05)
        impatient.cancel()
        patient = asyncio.ensure_future(hub.timeline())
        go.set()
        timeline = await asyncio.wait_for(patient, timeout=30.0)
        assert timeline.item_count > 0
    finally:
        hub.close()


def test_no_more_timelines_are_built_at_once_than_the_limit() -> None:
    limits = ReplayLimits(builds=2, analyses=1)
    running = 0
    peak = 0
    lock = threading.Lock()

    def build() -> ReplayTimeline:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.1)
        with lock:
            running -= 1
        return ReplayTimeline.empty(KEY)

    hubs = [ReplayHub(KEY, build=build, limits=limits) for _ in range(6)]

    async def all_built() -> list[ReplayTimeline]:
        return await asyncio.gather(*(hub.timeline() for hub in hubs))

    try:
        built = asyncio.run(asyncio.wait_for(all_built(), timeout=10.0))
    finally:
        for hub in hubs:
            hub.close()

    assert len(built) == 6
    assert peak == 2


def test_a_hub_is_kept_for_its_grace_period_then_retired() -> None:
    hubs = ReplayHubs(grace_s=0.2)
    hub = hubs.acquire("k", lambda: ReplayHub(KEY, build=lambda: ReplayTimeline.empty(KEY)))
    hubs.release(hub)

    again = hubs.acquire("k", lambda: pytest.fail("a returning viewer rebuilt the replay"))
    assert again is hub
    hubs.release(again)
    deadline = time.monotonic() + 5.0
    while hubs.active() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert hubs.active() == 0 and hub.stopped


def test_an_old_hub_takes_no_new_viewers_but_keeps_its_own() -> None:
    clock = _FakeClock()
    hubs = ReplayHubs(grace_s=60.0, max_age_s=3600.0, clock=clock.now)

    def make() -> ReplayHub:
        return ReplayHub(KEY, build=lambda: ReplayTimeline.empty(KEY))

    old = hubs.acquire("k", make)
    clock.value = 3601.0
    new = hubs.acquire("k", make)
    assert new is not old, "an hour-old replay must not take a new viewer"
    assert not old.stopped, "its own viewer is still watching"

    hubs.release(old)
    assert old.stopped, "a replaced hub goes as soon as its last viewer does"
    hubs.release(new)
    assert not new.stopped, "the current one waits out its grace period"
    hubs.stop_all()


def test_a_failed_build_is_not_shared_again() -> None:
    hubs = ReplayHubs(grace_s=60.0)

    def broken() -> ReplayTimeline:
        raise RuntimeError("the parquet is missing")

    failed = hubs.acquire("k", lambda: ReplayHub(KEY, build=broken))
    with pytest.raises(RuntimeError):
        asyncio.run(failed.timeline())
    fresh = hubs.acquire("k", lambda: ReplayHub(KEY, build=lambda: ReplayTimeline.empty(KEY)))
    assert fresh is not failed
    hubs.stop_all()


def test_the_route_serves_concurrent_viewers_from_one_shared_replay(client: TestClient) -> None:
    url = f"/stream/{SESSION_KEY}?mode=replay&speed=1"
    with client.websocket_connect(url) as first:
        first.receive_json()
        with client.websocket_connect(url) as second:
            second.receive_json()
            assert REPLAY_HUBS.active() == 1


def test_stamps_follow_the_viewer_who_changes_speed_only() -> None:
    """Two viewers on one timeline: one speeds up mid-race, the other is
    untouched by it."""
    timeline = _timeline()
    timeline.start_analysis()
    timeline.wait_analysed(30.0)
    clocks = [_FakeClock(), _FakeClock()]
    paces = [ReplayPace(1.0), ReplayPace(1.0)]
    cursors = [
        ReplayCursor(timeline, pace, clock=clock.now, wall_clock=lambda: WALL)
        for pace, clock in zip(paces, clocks, strict=True)
    ]
    laps: list[list[float]] = [[], []]
    try:
        for index, (cursor, clock) in enumerate(zip(cursors, clocks, strict=True)):
            while len(laps[index]) < 60:
                due = cursor.due(limit=1)
                for text in due.texts:
                    message = json.loads(text)
                    if message["kind"] == "lap_completed":
                        laps[index].append(_wall(message))
                        if index == 0 and len(laps[0]) == 20:
                            paces[0].set_speed(16.0)
                if due.wait_s is None:
                    break
                if due.wait_s > 0:
                    clock.sleep(due.wait_s)
    finally:
        timeline.close()

    untouched, changed = laps[1], laps[0]
    assert untouched[:20] == changed[:20]
    later_untouched = untouched[59] - untouched[25]
    later_changed = changed[59] - changed[25]
    assert later_changed == pytest.approx(later_untouched / 16, rel=0.05)


def test_the_wall_clock_label_is_the_viewers_start_plus_its_offset() -> None:
    timeline = _timeline()
    try:
        clock = _FakeClock()
        cursor = ReplayCursor(
            timeline, ReplayPace(2.0, delay=3.0), clock=clock.now, wall_clock=lambda: WALL
        )
        due = cursor.due(limit=1)
        message = json.loads(due.texts[0])
    finally:
        timeline.close()

    first = timeline.item(0)
    expected = WALL + timedelta(seconds=3.0 + first.elapsed_s / 2.0)
    assert datetime.fromisoformat(message["event_time_utc"]) == expected
