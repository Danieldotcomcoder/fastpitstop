"""`f1-watchdog`: tells someone when a live session's stream goes quiet.

The race-weekend failure that costs the most is the one nobody notices: the
worker stalls, or the hub stops following the store, and every viewer's
dashboard freezes while the TV carries on. This runs beside the API (the
`watchdog` compose service) as a synthetic viewer: every `poll_s` it asks
`/sessions` which sessions are live, opens each one's stream exactly as a
browser does (`/stream/{key}?mode=live`), and raises an alert when nothing
has arrived for `stall_after_s`, then another when traffic resumes.

Alerts always go to the log (`docker compose logs watchdog`) and, when SMTP
is configured (the same `F1_SMTP_*` settings the feedback form uses), by
email to `F1_ALERT_TO`, falling back to `F1_FEEDBACK_TO`.

The default threshold is a minute, not seconds: a red-flag stoppage can be
legitimately quiet, and an alert that cries wolf every red flag gets
ignored. `StallTracker` is the whole decision, pure and unit-tested; the
rest is plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import websockets

from f1_api.alerts import send_alert
from f1_api.config import Settings, get_settings

_log = logging.getLogger("f1_api.watchdog")

DEFAULT_STALL_AFTER_S = 60.0
DEFAULT_POLL_S = 30.0
RECEIVE_TICK_S = 5.0
"""How often a quiet stream is re-checked against the threshold."""

Alert = Callable[[str, str, str], Awaitable[None] | None]
"""`(event, session_key, detail)`: event is `stalled`, `recovered` or
`unreachable`."""


@dataclass
class StallTracker:
    """When a stream has been quiet too long, once per silence."""

    stall_after_s: float
    last_heard: float
    stalled: bool = False

    def heard(self, now: float) -> str | None:
        """Something arrived. `recovered` if this ends a reported stall."""
        self.last_heard = now
        if self.stalled:
            self.stalled = False
            return "recovered"
        return None

    def check(self, now: float) -> str | None:
        """`stalled` the first time the silence passes the threshold."""
        if not self.stalled and now - self.last_heard >= self.stall_after_s:
            self.stalled = True
            return "stalled"
        return None


async def watch_stream(
    url: str,
    key: str,
    alert: Alert,
    *,
    stall_after_s: float = DEFAULT_STALL_AFTER_S,
    tick_s: float = RECEIVE_TICK_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Follow one live stream until cancelled, alerting on stalls. A dropped
    connection is retried; failing to get one back for `stall_after_s` is
    itself an alert (`unreachable`), since viewers cannot get one either."""
    tracker = StallTracker(stall_after_s, last_heard=clock())
    unreachable = False
    while True:
        try:
            async with websockets.connect(url, open_timeout=10, ping_interval=20) as ws:
                if unreachable:
                    unreachable = False
                    await _fire(alert, "recovered", key, "the stream accepts connections again")
                while True:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=tick_s)
                        event = tracker.heard(clock())
                    except TimeoutError:
                        event = tracker.check(clock())
                    if event is not None:
                        quiet = clock() - tracker.last_heard
                        detail = (
                            f"nothing for {quiet:.0f}s"
                            if event == "stalled"
                            else "envelopes are arriving again"
                        )
                        await _fire(alert, event, key, detail)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not unreachable and clock() - tracker.last_heard >= stall_after_s:
                unreachable = True
                await _fire(alert, "unreachable", key, f"cannot connect: {exc}")
            await asyncio.sleep(min(tick_s, 5.0))


async def _fire(alert: Alert, event: str, key: str, detail: str) -> None:
    result = alert(event, key, detail)
    if result is not None:
        await result


def stream_url(api: str, key: str) -> str:
    base = api.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return f"{base}/stream/{quote(key, safe='')}?mode=live"


async def live_session_keys(client: httpx.AsyncClient, api: str) -> set[str]:
    response = await client.get(f"{api.rstrip('/')}/sessions", timeout=15)
    response.raise_for_status()
    return {str(row["key"]) for row in response.json() if row.get("live")}


def build_alert(settings: Settings) -> Alert:
    """Log every alert; email it too when SMTP is configured."""

    def alert(event: str, key: str, detail: str) -> None:
        line = f"live stream {event}: {key} ({detail})"
        if event == "recovered":
            _log.info(line)
        else:
            _log.error(line)
        send_alert(
            settings,
            f"live stream {event}: {key}",
            f"{line}\n\nCheck `docker compose logs worker api` on the VM."
            "\nThis message is from f1-watchdog.",
        )

    return alert


async def run(api: str, alert: Alert, *, stall_after_s: float, poll_s: float) -> None:
    """Watch every live session the API lists, starting and stopping a
    watcher as sessions go live and finish."""
    watchers: dict[str, asyncio.Task[None]] = {}
    async with httpx.AsyncClient() as client:
        while True:
            try:
                live = await live_session_keys(client, api)
            except Exception:
                _log.warning("could not list sessions; retrying", exc_info=True)
                live = set(watchers)
            for key in live - set(watchers):
                _log.info("watching live session %s", key)
                watchers[key] = asyncio.create_task(
                    watch_stream(stream_url(api, key), key, alert, stall_after_s=stall_after_s)
                )
            for key in set(watchers) - live:
                _log.info("session %s is no longer live", key)
                watchers.pop(key).cancel()
            await asyncio.sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f1-watchdog", description=__doc__)
    parser.add_argument("--api", default="http://api:8000", help="the API's base URL")
    parser.add_argument("--stall-after", type=float, default=DEFAULT_STALL_AFTER_S)
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_S)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs every request at INFO: one line per poll, ~2,900 a day of noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    alert = build_alert(get_settings())
    asyncio.run(run(args.api, alert, stall_after_s=args.stall_after, poll_s=args.poll))
    return 0


__all__ = [
    "DEFAULT_POLL_S",
    "DEFAULT_STALL_AFTER_S",
    "StallTracker",
    "build_alert",
    "live_session_keys",
    "main",
    "run",
    "stream_url",
    "watch_stream",
]
