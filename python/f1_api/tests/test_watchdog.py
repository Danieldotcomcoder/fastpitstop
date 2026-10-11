"""`f1_api.watchdog`: the stall decision itself, and a real stalled stream.

The stream test runs a real local WebSocket server that sends a few
envelopes, goes quiet past the threshold (a stalled worker), then sends
again — the "deliberately stalled rehearsal" docs/ROADMAP.md W0 asks for,
in miniature and with no network."""

from __future__ import annotations

import asyncio
import contextlib
import socket

import websockets

from f1_api.watchdog import StallTracker, stream_url, watch_stream


def test_a_quiet_stream_is_reported_once_and_its_recovery_once() -> None:
    tracker = StallTracker(stall_after_s=60, last_heard=0)
    assert tracker.check(59.9) is None
    assert tracker.check(60.0) == "stalled"
    assert tracker.check(120.0) is None, "one alert per silence"
    assert tracker.heard(121.0) == "recovered"
    assert tracker.heard(122.0) is None
    assert tracker.check(150.0) is None


def test_stream_urls_follow_the_api_scheme() -> None:
    assert stream_url("http://api:8000", "2026-17-race") == (
        "ws://api:8000/stream/2026-17-race?mode=live"
    )
    assert stream_url("https://fastpitstop.online/api/", "2026-17-race").startswith(
        "wss://fastpitstop.online/api/stream/"
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_stalled_stream_raises_an_alert_and_its_return_clears_it() -> None:
    alerts: list[tuple[str, str]] = []
    resume = asyncio.Event()

    async def feed(ws: websockets.ServerConnection) -> None:
        for n in range(3):
            await ws.send(f'{{"kind": "gap_updated", "n": {n}}}')
            await asyncio.sleep(0.05)
        await resume.wait()  # the worker stalls here
        for n in range(3):
            await ws.send(f'{{"kind": "gap_updated", "n": {n}}}')
            await asyncio.sleep(0.05)
        await asyncio.sleep(5)

    async def scenario() -> None:
        port = _free_port()
        async with websockets.serve(feed, "127.0.0.1", port):

            def alert(event: str, key: str, detail: str) -> None:
                alerts.append((event, key))
                if event == "stalled":
                    resume.set()

            watcher = asyncio.create_task(
                watch_stream(
                    f"ws://127.0.0.1:{port}/stream/k?mode=live",
                    "2026-17-race",
                    alert,
                    stall_after_s=0.6,
                    tick_s=0.05,
                )
            )
            for _ in range(100):
                if ("recovered", "2026-17-race") in alerts:
                    break
                await asyncio.sleep(0.05)
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    asyncio.run(scenario())
    assert alerts == [("stalled", "2026-17-race"), ("recovered", "2026-17-race")]


def test_a_stream_nobody_can_connect_to_is_an_alert() -> None:
    alerts: list[str] = []

    async def scenario() -> None:
        watcher = asyncio.create_task(
            watch_stream(
                f"ws://127.0.0.1:{_free_port()}/stream/k?mode=live",
                "2026-17-race",
                lambda event, key, detail: alerts.append(event),
                stall_after_s=0.3,
                tick_s=0.05,
            )
        )
        for _ in range(60):
            if alerts:
                break
            await asyncio.sleep(0.05)
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    asyncio.run(scenario())
    assert alerts == ["unreachable"]
