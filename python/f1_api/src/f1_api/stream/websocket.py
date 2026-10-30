"""`/stream/{key}` — the WebSocket route. `mode=replay` (the default, and
the one Phase 3 actually develops against per CLAUDE.md) pumps a session's
own event stream back out at `speed`; `mode=live` polls the store for a
session currently being ingested.

`mode=live` reads one shared hub per running session
(`f1_api.stream.live`): the store is polled, and the kernel and narrator
run, once per session however many people are watching, and a viewer holds
no database connection of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Callable, Collection, Generator, Hashable
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from f1_api.calls import enabled_call_kinds
from f1_api.config import Settings, get_settings
from f1_api.geometry import session_positions
from f1_api.radio import radio_store_cm
from f1_api.sessions import parse_session_key, resolve_session
from f1_api.stream.live import LIVE_HUBS, LiveDeps, LiveHub
from f1_api.stream.replay import (
    DEFAULT_STREAM_KINDS,
    POSITION_KIND,
    ReplayCursor,
    ReplayPace,
    ReplayTimeline,
)
from f1_api.stream.replay_hub import REPLAY_HUBS, ReplayHub, WorkerPlan
from f1_api.stream.replay_worker import WorkerJob
from f1_api.why import (
    ExplanationSchedule,
    ScheduledExplanation,
    narrate_story,
    precompute_session_explanations,
    why_stream_deps_cm,
)
from f1_domain import SessionKey
from f1_kernel import EventSource
from f1_kernel.circuit import circuit_for_session
from f1_radio.search import SearchIndex
from f1_why import EvidenceBundle, ExplanationStore, Narrator

_log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

LIVE_FANOUT_INTERVAL_S = 0.25
"""How often a live viewer picks up what its session's hub has published.
A read of an in-memory list: costs nothing next to the hub's own poll."""


MAX_REPLAY_SPEED = 100.0
"""The fastest a viewer can ask a replay to run, as a speed message."""


def replay_speed_from(text: str) -> float | None:
    """The speed in a client's `{"type": "speed", "speed": N}` message, or
    None for anything else (ignored, never an error: the socket carries
    no other client messages)."""
    try:
        message = json.loads(text)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    fields = cast(dict[str, object], message)
    if fields.get("type") != "speed":
        return None
    value = fields.get("speed")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0 or value > MAX_REPLAY_SPEED:
        return None
    return float(value)


async def _client_gone(websocket: WebSocket, on_text: Callable[[str], None] | None = None) -> None:
    """Completes when the client disconnects, handing any text the client
    sends meanwhile to `on_text` (a replay's speed changes).

    `WebSocketDisconnect` is only ever raised from a RECEIVE, and the live
    loop never receives -- so before this existed, every browser tab that
    opened the dashboard left a `while True` poll running on the server for
    the lifetime of the process. Twenty-four of them accumulated between two
    deploys and pinned the API at 93% CPU, which is what made `/sessions`
    take 40 seconds and the dashboard look dead.
    """
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if on_text is not None and isinstance(text, str):
                on_text(text)
    except Exception:
        return


@router.websocket("/stream/{key}")
async def stream(
    websocket: WebSocket,
    key: str,
    settings: Annotated[Settings, Depends(get_settings)],
    mode: Literal["replay", "live"] = Query("replay"),
    speed: float = Query(1.0, ge=0),
    include_telemetry: bool = Query(
        False,
        description=(
            "Include car_telemetry envelopes (off by default — see "
            "f1_api.stream.replay's module docstring for why: a real "
            "race's resampled telemetry dwarfs every other event kind "
            "combined, and no shipped view consumes it off this stream)."
        ),
    ),
    history: float = Query(
        0.0,
        ge=0.0,
        le=24 * 3600.0,
        description=(
            "mode=live only: how far behind live the viewer runs, in seconds "
            "(their Delay Sync delay). Gap history this far back arrives in "
            "full; older gaps are reduced to each driver's latest."
        ),
    ),
    anchor_to_first_lap: bool = Query(
        True,
        description=(
            "Pace mode=replay from the first lap_completed ('lights out') "
            "rather than the session's raw recording start (on by default "
            "— see f1_api.stream.replay's module docstring: a real Race "
            "session's own data starts roughly an hour before lights out). "
            "Every pre-anchor envelope still arrives, just in an immediate "
            "burst rather than paced across that hour; pass false for the "
            "raw, unanchored pacing."
        ),
    ),
) -> None:
    kinds: Collection[str] = (
        DEFAULT_STREAM_KINDS | {"car_telemetry"} if include_telemetry else DEFAULT_STREAM_KINDS
    )
    try:
        await websocket.accept()
    except RuntimeError:
        # The client disconnected between the handshake starting and this
        # `accept()` actually running — uvicorn refuses to send
        # `websocket.accept` once it has already observed the disconnect
        # (ASGI spec: only `websocket.close` is valid after that). Nothing
        # left to serve; the symmetric race on the way OUT is already
        # handled the same way by `_safe_close` below.
        return
    try:
        session_key = parse_session_key(key)
    except ValueError as exc:
        await websocket.close(code=4400, reason=str(exc))
        return

    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            await websocket.close(code=4404, reason="session not found")
            return
        entry = resolved.entry

    if mode == "replay":
        # Every viewer of a finished session walks one shared replay (see
        # `f1_api.stream.replay_hub`). A session still running is replayed
        # as it stands now, for this viewer alone: its events are still
        # arriving, so nobody else's copy would be the same.
        replay = _ReplaySpec(
            settings,
            session_key,
            event_name=entry.event_name,
            circuit_name=entry.circuit_name or entry.location,
            kinds=kinds,
            anchor_to_first_lap=anchor_to_first_lap,
        )
        if entry.live:
            hub = replay.make_hub(shared=False)
            try:
                await _stream_replay(websocket, hub, speed=speed)
            finally:
                hub.close()
            return
        hub = REPLAY_HUBS.acquire(replay.key, lambda: replay.make_hub(shared=True))
        try:
            await _stream_replay(websocket, hub, speed=speed)
        finally:
            REPLAY_HUBS.release(hub)
        return

    # Live: the session's connection is released above. Every viewer reads
    # the one shared hub for this session (see `f1_api.stream.live`), so a
    # viewer costs no database connection and no kernel of its own.
    if not entry.live:
        # Not merely "not in the store": a session that has been ingested
        # but is over must be refused too. Accepting one replayed a finished
        # race's whole event log through the kernel — and its anomalies
        # through the narration model — once per connected tab.
        await websocket.close(
            code=4404,
            reason="mode=live requires a session that is currently running",
        )
        return
    hub = LIVE_HUBS.acquire(
        session_key,
        kinds,
        lambda: _make_live_hub(settings, session_key, entry.event_name, kinds),
    )
    try:
        await _stream_live(websocket, hub, history_s=history)
    finally:
        LIVE_HUBS.release(hub)


def _make_live_hub(
    settings: Settings,
    session_key: SessionKey,
    event_name: str | None,
    kinds: Collection[str] | None,
) -> LiveHub:
    @contextlib.contextmanager
    def open_source() -> Generator[EventSource | None, None, None]:
        with resolve_session(settings, session_key) as resolved:
            yield resolved.source if resolved is not None else None

    @contextlib.contextmanager
    def open_deps() -> Generator[LiveDeps, None, None]:
        with (
            radio_store_cm(settings) as radio_store,
            why_stream_deps_cm(settings) as (model, store, radio_index),
        ):
            yield radio_store, model, store, radio_index

    return LiveHub(
        session_key,
        settings=settings,
        event_name=event_name,
        kinds=kinds,
        open_source=open_source,
        open_deps=open_deps,
    )


def _build_explanation_schedule(
    schedule: ExplanationSchedule,
    settings: Settings,
    session_key: SessionKey,
    *,
    event_name: str | None,
    narration_model: Narrator,
    explanation_store: ExplanationStore,
    radio_index: SearchIndex | None,
) -> None:
    """Worker-thread body: build this session's explanation batch and hand
    it to `schedule`.

    It resolves its OWN source rather than borrowing the streaming
    coroutine's, because a `StoreSource` wraps a single database connection
    that is not safe to use from two threads at once — the historical case
    would be fine sharing, but a rule that only holds for one backing store
    is the kind that breaks the first time a live session is streamed.

    Never raises into the caller: a failure here means a race streams
    without explanations, which is strictly better than a race that does
    not stream at all."""
    try:
        with resolve_session(settings, session_key) as resolved:
            if resolved is None:
                schedule.set([])
                return
            schedule.set(
                precompute_session_explanations(
                    resolved.source,
                    session_key,
                    narration_model,
                    explanation_store,
                    radio_index=radio_index,
                    event_name=event_name,
                    max_explanations=settings.max_explanations,
                )
            )
    except BaseException as exc:  # degrade, never break the stream (see docstring)
        _log.warning("explanation precompute failed for %s", session_key, exc_info=True)
        schedule.fail(exc)


class _ReplaySpec:
    """What one replay is (a session, as some settings serve it, with some
    stream kinds and anchor), and how to build its hub."""

    def __init__(
        self,
        settings: Settings,
        session_key: SessionKey,
        *,
        event_name: str | None,
        circuit_name: str | None,
        kinds: Collection[str] | None,
        anchor_to_first_lap: bool,
    ) -> None:
        self.settings = settings
        self.session_key = session_key
        self.event_name = event_name
        self.circuit_name = circuit_name
        self.kinds = kinds
        self.anchor_to_first_lap = anchor_to_first_lap

    @property
    def key(self) -> Hashable:
        # The settings are part of it: they decide where the session's data
        # comes from (the tests serve the same key from different fixtures).
        kinds = frozenset(self.kinds) if self.kinds is not None else None
        return (self.settings, self.session_key, kinds, self.anchor_to_first_lap)

    def make_hub(self, *, shared: bool) -> ReplayHub:
        if shared:
            # Analysed in a worker process, which reads the events again
            # itself, so the timeline need not hold them.
            return ReplayHub(
                self.session_key,
                build=lambda: self._build(keep_events=False),
                worker=WorkerPlan(job=self._job, narrate=self._narrate),
            )
        # A running session is still growing: a worker reading it again
        # would find more events than the timeline holds. Analysed here.
        return ReplayHub(
            self.session_key,
            build=lambda: self._build(keep_events=True),
            explain=self._explain,
        )

    def _build(self, *, keep_events: bool) -> ReplayTimeline:
        """On the build pool, with its own source: a store source wraps one
        database connection, never shared between threads."""
        settings, session_key = self.settings, self.session_key
        with resolve_session(settings, session_key) as resolved:
            if resolved is None:
                return ReplayTimeline.empty(session_key)
            source = resolved.source
            wants_positions = self.kinds is None or POSITION_KIND in self.kinds
            positions = (
                session_positions(
                    settings,
                    source,
                    session_key,
                    circuit_for_session(self.event_name, self.circuit_name),
                )[1]
                if wants_positions
                else None
            )
            with radio_store_cm(settings) as radio_store:
                return ReplayTimeline.build(
                    source,
                    session_key,
                    kinds=self.kinds,
                    anchor_to_first_lap=self.anchor_to_first_lap,
                    radio_store=radio_store,
                    event_name=self.event_name,
                    positions=positions,
                    call_kinds=enabled_call_kinds(settings),
                    keep_events=keep_events,
                )

    def _job(self, timeline: ReplayTimeline) -> WorkerJob:
        kinds = frozenset(self.kinds) if self.kinds is not None else None
        return WorkerJob(self.settings, timeline.spec(), kinds, story=True)

    def _narrate(self, bundles: list[EvidenceBundle]) -> list[ScheduledExplanation]:
        with why_stream_deps_cm(self.settings) as (model, store, _radio_index):
            return narrate_story(self.session_key, bundles, model, store)

    def _explain(self, schedule: ExplanationSchedule) -> None:
        with why_stream_deps_cm(self.settings) as (model, store, radio_index):
            _build_explanation_schedule(
                schedule,
                self.settings,
                self.session_key,
                event_name=self.event_name,
                narration_model=model,
                explanation_store=store,
                radio_index=radio_index,
            )


async def _stream_replay(websocket: WebSocket, hub: ReplayHub, *, speed: float) -> None:
    """One viewer of a replay: wait for the hub's timeline, then walk it at
    this viewer's own pace, on the event loop. No thread per viewer: the
    cursor never blocks, it says how long until the next envelope is due,
    and the wait wakes early for a speed change or the viewer leaving.
    Sending straight from here also means a client that reads slowly slows
    its own replay, never the server's memory."""
    # The viewer can change the speed mid-replay ("speed" messages, see
    # `replay_speed_from`): the replay carries on from the current moment at
    # the new rate instead of starting again.
    pace = ReplayPace(speed)
    woken = asyncio.Event()

    def on_text(text: str) -> None:
        new_speed = replay_speed_from(text)
        if new_speed is not None:
            pace.set_speed(new_speed)
            woken.set()

    # Sending into a closed socket does not raise: it logs "socket.send()
    # raised exception" and carries on. Without watching the receive side a
    # replay outlived its tab by the rest of the race, as `_stream_live`
    # once did (see `_client_gone`).
    gone = asyncio.ensure_future(_client_gone(websocket, on_text))
    gone.add_done_callback(lambda _: woken.set())
    try:
        ready = asyncio.ensure_future(hub.timeline())
        await asyncio.wait({ready, gone}, return_when=asyncio.FIRST_COMPLETED)
        if not ready.done():
            ready.cancel()
            return
        try:
            timeline = ready.result()
        except Exception:
            _log.warning("replay of %s could not be built", hub.session_key, exc_info=True)
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011, reason="replay unavailable")
            return
        cursor = ReplayCursor(timeline, pace)
        while not gone.done():
            # Cleared before looking, so a change while sending still wakes
            # the wait below.
            woken.clear()
            due = cursor.due()
            for text in due.texts:
                await websocket.send_text(text)
            if due.wait_s is None:
                break
            if due.wait_s > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(woken.wait(), timeout=due.wait_s)
    except (WebSocketDisconnect, RuntimeError, OSError):
        # The viewer went away mid-send; their replay simply ends.
        pass
    finally:
        gone.cancel()
        await _safe_close(websocket)


async def _stream_live(
    websocket: WebSocket,
    hub: LiveHub,
    *,
    history_s: float = 0.0,
    interval_s: float = LIVE_FANOUT_INTERVAL_S,
) -> None:
    """One viewer of a live session: everything the hub has published so
    far (the race as it stands, every snapshot and explanation already
    made), then the tail as it grows. Joining at lap 40 costs one burst of
    sends, not forty kernel recomputes."""
    first, sent = hub.join(history_s=history_s)
    gone = asyncio.ensure_future(_client_gone(websocket))
    try:
        for text in first:
            if gone.done():
                return
            await websocket.send_text(text)
        while not gone.done():
            pending = hub.since(sent)
            for text in pending:
                await websocket.send_text(text)
            sent += len(pending)
            # Wakes early when the client disconnects, so a closed tab stops
            # costing anything within milliseconds rather than forever.
            await asyncio.wait({gone}, timeout=interval_s)
    except (WebSocketDisconnect, RuntimeError, OSError):
        # The viewer went away mid-send (a closed tab, a dropped network):
        # uvicorn raises from the write rather than the receive. Their
        # stream simply ends; the hub and every other viewer carry on.
        pass
    finally:
        gone.cancel()
        await _safe_close(websocket)


async def _safe_close(websocket: WebSocket) -> None:
    with contextlib.suppress(RuntimeError):  # already closed by the client disconnecting
        await websocket.close()


__all__ = ["router"]
