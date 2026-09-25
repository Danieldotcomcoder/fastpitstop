"""`/stream/{key}` — the WebSocket route. `mode=replay` (the default, and
the one Phase 3 actually develops against per CLAUDE.md) pumps a session's
own event stream back out at `speed`; `mode=live` polls the store for a
session currently being ingested.

`mode=live` is deliberately the thinner of the two: per docs/PHASE0.md, the
live feed only exists ~24 weekends/year and its connection layer is not
unit-tested, so day-to-day development (and this package's own test suite)
targets `mode=replay` exclusively. `mode=live` here is a straightforward
DB-polling loop rather than a push (`LISTEN`/`NOTIFY`) pipe — correct, and
enough to unblock a frontend once a real live session exists to develop it
against, but not the last word on efficiency.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Collection, Iterator
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from f1_api.config import Settings, get_settings
from f1_api.geometry import session_positions
from f1_api.radio import radio_store_cm
from f1_api.schemas import StreamEnvelope
from f1_api.sessions import parse_session_key, resolve_session
from f1_api.stream.replay import (
    DEFAULT_STREAM_KINDS,
    POSITION_KIND,
    LapSnapshotFeed,
    car_positions_envelope,
    envelope_for_event,
    explanation_envelope,
    kernel_snapshot_envelope,
    live_position_frames,
    replay_envelopes,
)
from f1_api.why import (
    ExplanationSchedule,
    cited_evidence_refs,
    explain_session_anomaly,
    most_salient,
    precompute_session_explanations,
    why_stream_deps_cm,
)
from f1_domain import DomainEvent, SessionKey
from f1_kernel import EventSource, PositionFrame
from f1_kernel.circuit import circuit_for_session
from f1_radio.search import SearchIndex
from f1_radio.store import TranscriptStore
from f1_why import AnomalyParams, ExplanationStore, NarrationModel, detect_anomalies
from f1_why import anomaly_id as why_anomaly_id

_log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

LIVE_POLL_INTERVAL_S = 0.5
"""How often the live path looks for new events.

Twice a second, not once: at one second the dashboard visibly stutters
against a broadcast. It is affordable only because the poll is incremental
(`_CursorSource` below) -- the previous full-session re-read could not have
survived being run twice as often.
"""


@runtime_checkable
class _CursorSource(Protocol):
    """An `EventSource` that can also resume from a cursor.

    `f1_kernel.StoreSource` implements this; parquet and in-memory sources do
    not, and fall back to the re-read path below. Structural rather than a
    concrete import so the fallback stays honest in tests.
    """

    def events_after(
        self,
        session_key: SessionKey,
        after: tuple[int, int] | None,
        *,
        kinds: Collection[str] | None = None,
    ) -> Iterator[tuple[DomainEvent, tuple[int, int]]]: ...


async def _client_gone(websocket: WebSocket) -> None:
    """Completes when the client disconnects.

    `WebSocketDisconnect` is only ever raised from a RECEIVE, and the live
    loop never receives -- so before this existed, every browser tab that
    opened the dashboard left a `while True` poll running on the server for
    the lifetime of the process. Twenty-four of them accumulated between two
    deploys and pinned the API at 93% CPU, which is what made `/sessions`
    take 40 seconds and the dashboard look dead.
    """
    try:
        while True:
            await websocket.receive()
    except Exception:
        return


class _Done:
    """Sentinel closing the producer-thread queue in `_stream_replay`."""


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

        with (
            radio_store_cm(settings) as radio_store,
            why_stream_deps_cm(settings) as (model, store, radio_index),
        ):
            if mode == "replay":
                await _stream_replay(
                    websocket,
                    resolved.source,
                    session_key,
                    settings=settings,
                    event_name=resolved.entry.event_name,
                    speed=speed,
                    kinds=kinds,
                    anchor_to_first_lap=anchor_to_first_lap,
                    radio_store=radio_store,
                    narration_model=model,
                    explanation_store=store,
                    radio_index=radio_index,
                    circuit_name=resolved.entry.circuit_name,
                )
            elif resolved.entry.live:
                await _stream_live(
                    websocket,
                    resolved.source,
                    session_key,
                    settings=settings,
                    event_name=resolved.entry.event_name,
                    kinds=kinds,
                    radio_store=radio_store,
                    narration_model=model,
                    explanation_store=store,
                    radio_index=radio_index,
                )
            else:
                # Not merely "not in the store": a session that has been
                # ingested but is over must be refused too. Accepting one
                # replayed a finished race's whole event log through the
                # kernel — and its anomalies through the narration model —
                # once per connected tab, for free, forever.
                await websocket.close(
                    code=4404,
                    reason="mode=live requires a session that is currently running",
                )


def _build_explanation_schedule(
    schedule: ExplanationSchedule,
    settings: Settings,
    session_key: SessionKey,
    *,
    event_name: str | None,
    narration_model: NarrationModel,
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


async def _stream_replay(
    websocket: WebSocket,
    source: EventSource,
    session_key: SessionKey,
    *,
    settings: Settings,
    event_name: str | None,
    speed: float,
    kinds: Collection[str] | None,
    anchor_to_first_lap: bool,
    radio_store: TranscriptStore | None,
    narration_model: NarrationModel,
    explanation_store: ExplanationStore,
    radio_index: SearchIndex | None,
    circuit_name: str | None = None,
) -> None:
    """`replay_envelopes` is synchronous and really sleeps (it shares
    `f1_replay`'s timing model), so it runs on a background thread; the
    thread hands items back to THIS coroutine's own event loop via
    `call_soon_threadsafe` rather than `run_in_executor` — the latter
    would borrow from the loop's shared default thread-pool executor,
    which a long-lived blocking `queue.get()` can starve out from under
    other work on the same loop.

    Still unbounded, still no backpressure on `queue`/`websocket.send_json`
    below — that was fine when this only ran against the checked-in test
    fixture (a genuine few-thousand-event race), but excluding
    `car_telemetry` by default (`kinds`, see `f1_api.stream.replay`'s
    module docstring) is what actually brings a REAL historical race back
    into that few-thousand-event range; it does not add backpressure. A
    client that still cannot keep up with the default (telemetry-excluded)
    stream, or a caller that opts into `include_telemetry=true`, can still
    starve the browser tab and, downstream, this coroutine's
    `websocket.send_json` calls the same way — that gap is real and not
    addressed here; see docs/PHASE3.md's "what's not covered yet".
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StreamEnvelope | _Done | BaseException] = asyncio.Queue()

    # Kicked off first and never waited on: the event stream below starts
    # flowing immediately and picks these up whenever they land.
    schedule = ExplanationSchedule()
    explainer = threading.Thread(
        target=_build_explanation_schedule,
        args=(schedule, settings, session_key),
        kwargs={
            "event_name": event_name,
            "narration_model": narration_model,
            "explanation_store": explanation_store,
            "radio_index": radio_index,
        },
        name="f1-api-explanations",
        daemon=True,
    )
    explainer.start()

    # Set when the viewer is gone: the producer stops at its next envelope
    # instead of pacing out the rest of the race into a queue nobody reads
    # (up to two hours of it at 1x, for every tab ever closed mid-replay).
    stop = threading.Event()

    def produce() -> None:
        try:
            wants_positions = kinds is None or POSITION_KIND in kinds
            positions = (
                session_positions(
                    settings, source, session_key, circuit_for_session(event_name, circuit_name)
                )[1]
                if wants_positions
                else None
            )
            with contextlib.closing(
                replay_envelopes(
                    source,
                    session_key,
                    speed=speed,
                    kinds=kinds,
                    anchor_to_first_lap=anchor_to_first_lap,
                    radio_store=radio_store,
                    explanation_schedule=schedule,
                    radio_index=radio_index,
                    event_name=event_name,
                    positions=positions,
                )
            ) as envelopes:
                for envelope in envelopes:
                    if stop.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, envelope)
        except BaseException as exc:  # re-raised on the consumer side below
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _Done())

    thread = threading.Thread(target=produce, name="f1-api-replay-stream", daemon=True)
    thread.start()
    # Sending into a closed socket does not raise: it logs "socket.send()
    # raised exception" and carries on. Without watching the receive side a
    # replay outlived its tab by the rest of the race, as `_stream_live`
    # once did (see `_client_gone`).
    gone = asyncio.ensure_future(_client_gone(websocket))
    try:
        while True:
            pending = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({pending, gone}, return_when=asyncio.FIRST_COMPLETED)
            if pending not in done:
                pending.cancel()
                break
            item = pending.result()
            if isinstance(item, _Done):
                break
            if isinstance(item, BaseException):
                raise item
            await websocket.send_json(item.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        gone.cancel()
        thread.join(timeout=1.0)
        await _safe_close(websocket)


async def _stream_live(
    websocket: WebSocket,
    source: EventSource,
    session_key: SessionKey,
    *,
    settings: Settings,
    event_name: str | None,
    kinds: Collection[str] | None,
    radio_store: TranscriptStore | None,
    narration_model: NarrationModel,
    explanation_store: ExplanationStore,
    radio_index: SearchIndex | None,
) -> None:
    """Live path: domain events + kernel snapshots as before, plus
    explanations generated on newly-detected anomalies against the same
    `explain_cached` store historical precompute uses. An explanation's
    `event_time_utc` is the anomaly's own (already-real) `anomaly_time` —
    Delay Sync gates it the same way as every other envelope.

    Explanations are budgeted exactly like the replay path
    (`Settings.max_explanations`): a live race detects anomalies at the same
    rate a replayed one does, and one model call each would blow both the
    cost budget and the poll interval. Once the budget is spent this keeps
    streaming timing and snapshots — the race stays watchable, it just
    stops narrating."""
    feed = LapSnapshotFeed(session_key, event_name=event_name)
    sent = 0
    cursor: tuple[int, int] | None = None
    emitted_anomaly_ids: set[str] = set()
    incremental = source if isinstance(source, _CursorSource) else None
    gone = asyncio.ensure_future(_client_gone(websocket))
    try:
        while not gone.done():
            if incremental is not None:
                batch = list(incremental.events_after(session_key, cursor, kinds=kinds))
                if batch:
                    cursor = batch[-1][1]
                events = sorted((event for event, _ in batch), key=lambda event: event.ts_utc)
            else:
                seen = sorted(source.events(session_key, kinds=kinds), key=lambda e: e.ts_utc)
                events = seen[sent:]
                sent = len(seen)
            for item in live_position_frames(events):
                if isinstance(item, PositionFrame):
                    await websocket.send_json(
                        car_positions_envelope(item.ts_utc, item).model_dump(mode="json")
                    )
                    continue
                event = item
                envelope = envelope_for_event(event, radio_store=radio_store)
                await websocket.send_json(envelope.model_dump(mode="json"))
                report = feed.feed(event)
                if report is not None:
                    if report.crossovers:
                        await websocket.send_json(
                            kernel_snapshot_envelope(event.ts_utc, report).model_dump(mode="json")
                        )
                    budget = settings.max_explanations - len(emitted_anomaly_ids)
                    for anomaly in most_salient(
                        detect_anomalies(report.frames, report, AnomalyParams()), budget
                    ):
                        why_id = why_anomaly_id(anomaly)
                        if why_id in emitted_anomaly_ids:
                            continue
                        if len(emitted_anomaly_ids) >= settings.max_explanations:
                            break
                        explanation, bundle = explain_session_anomaly(
                            source,
                            session_key,
                            anomaly,
                            narration_model,
                            explanation_store,
                            report=report,
                            radio_index=radio_index,
                            event_name=event_name,
                        )
                        cited = cited_evidence_refs(session_key, bundle, explanation)
                        await websocket.send_json(
                            explanation_envelope(
                                anomaly.anomaly_time,
                                session_key,
                                anomaly,
                                explanation,
                                cited,
                            ).model_dump(mode="json")
                        )
                        emitted_anomaly_ids.add(why_id)
            # Wakes early when the client disconnects, so a closed tab stops
            # costing a poll within milliseconds rather than forever.
            await asyncio.wait({gone}, timeout=LIVE_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        pass
    finally:
        gone.cancel()
        await _safe_close(websocket)


async def _safe_close(websocket: WebSocket) -> None:
    with contextlib.suppress(RuntimeError):  # already closed by the client disconnecting
        await websocket.close()


__all__ = ["router"]
