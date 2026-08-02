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
import threading
from collections.abc import Collection
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from f1_api.config import Settings, get_settings
from f1_api.radio import radio_store_cm
from f1_api.schemas import StreamEnvelope
from f1_api.sessions import parse_session_key, resolve_session
from f1_api.stream.replay import (
    DEFAULT_STREAM_KINDS,
    LapSnapshotFeed,
    envelope_for_event,
    explanation_envelope,
    kernel_snapshot_envelope,
    replay_envelopes,
)
from f1_api.why import (
    cited_evidence_refs,
    explain_session_anomaly,
    why_stream_deps_cm,
)
from f1_domain import SessionKey
from f1_kernel import EventSource
from f1_radio.search import SearchIndex
from f1_radio.store import TranscriptStore
from f1_why import AnomalyParams, ExplanationStore, NarrationModel, detect_anomalies
from f1_why import anomaly_id as why_anomaly_id

router = APIRouter(tags=["stream"])

LIVE_POLL_INTERVAL_S = 1.0


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
    kinds: Collection[str] | None = None if include_telemetry else DEFAULT_STREAM_KINDS
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
                    speed=speed,
                    kinds=kinds,
                    anchor_to_first_lap=anchor_to_first_lap,
                    radio_store=radio_store,
                    narration_model=model,
                    explanation_store=store,
                    radio_index=radio_index,
                )
            elif resolved.entry.provenance == "live":
                await _stream_live(
                    websocket,
                    resolved.source,
                    session_key,
                    kinds=kinds,
                    radio_store=radio_store,
                    narration_model=model,
                    explanation_store=store,
                    radio_index=radio_index,
                )
            else:
                await websocket.close(
                    code=4404,
                    reason="mode=live requires a session currently in the live store",
                )


async def _stream_replay(
    websocket: WebSocket,
    source: EventSource,
    session_key: SessionKey,
    *,
    speed: float,
    kinds: Collection[str] | None,
    anchor_to_first_lap: bool,
    radio_store: TranscriptStore | None,
    narration_model: NarrationModel,
    explanation_store: ExplanationStore,
    radio_index: SearchIndex | None,
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

    def produce() -> None:
        try:
            for envelope in replay_envelopes(
                source,
                session_key,
                speed=speed,
                kinds=kinds,
                anchor_to_first_lap=anchor_to_first_lap,
                radio_store=radio_store,
                narration_model=narration_model,
                explanation_store=explanation_store,
                radio_index=radio_index,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, envelope)
        except BaseException as exc:  # re-raised on the consumer side below
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _Done())

    thread = threading.Thread(target=produce, name="f1-api-replay-stream", daemon=True)
    thread.start()
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _Done):
                break
            if isinstance(item, BaseException):
                raise item
            await websocket.send_json(item.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        thread.join(timeout=1.0)
        await _safe_close(websocket)


async def _stream_live(
    websocket: WebSocket,
    source: EventSource,
    session_key: SessionKey,
    *,
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
    Delay Sync gates it the same way as every other envelope."""
    feed = LapSnapshotFeed(session_key)
    sent = 0
    emitted_anomaly_ids: set[str] = set()
    try:
        while True:
            events = sorted(source.events(session_key, kinds=kinds), key=lambda event: event.ts_utc)
            for event in events[sent:]:
                envelope = envelope_for_event(event, radio_store=radio_store)
                await websocket.send_json(envelope.model_dump(mode="json"))
                report = feed.feed(event)
                if report is not None:
                    if report.crossovers:
                        await websocket.send_json(
                            kernel_snapshot_envelope(event.ts_utc, report).model_dump(mode="json")
                        )
                    for anomaly in detect_anomalies(report.frames, report, AnomalyParams()):
                        why_id = why_anomaly_id(anomaly)
                        if why_id in emitted_anomaly_ids:
                            continue
                        explanation, bundle = explain_session_anomaly(
                            source,
                            session_key,
                            anomaly,
                            narration_model,
                            explanation_store,
                            report=report,
                            radio_index=radio_index,
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
            sent = len(events)
            await asyncio.sleep(LIVE_POLL_INTERVAL_S)
    except WebSocketDisconnect:
        pass
    finally:
        await _safe_close(websocket)


async def _safe_close(websocket: WebSocket) -> None:
    with contextlib.suppress(RuntimeError):  # already closed by the client disconnecting
        await websocket.close()


__all__ = ["router"]
