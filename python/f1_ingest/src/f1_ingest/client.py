"""LiveFeedClient: the real SignalR connection to F1's live timing feed.

NOT unit tested — see the package docstring in `f1_ingest/__init__.py` and
the Phase 0 runbook: this can only be meaningfully exercised against the
real feed, which only exists during a live session (~24 weekends/year).

This subclasses FastF1's `fastf1.livetiming.client.SignalRClient` rather
than reimplementing the SignalR Core negotiation (the pre-negotiate cookie
handshake, the F1TV auth-token factory) from scratch — see CLAUDE.md's data
source note: "do NOT reverse-engineer the SignalR negotiation from
scratch... verify topic names against FastF1's current source since the
stream is unofficial and can change." We only override `_on_message` to
route data into an `f1_ingest.RecordingSession` instead of writing
formatted text to a file.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from fastf1.livetiming.client import SignalRClient
from signalrcore.messages.completion_message import CompletionMessage

from f1_ingest.topics import default_topics

logger = logging.getLogger(__name__)


class LiveFeedClient(SignalRClient):
    """Connects to the live feed and dispatches messages to callbacks.

    Unlike the parent class's `start()` (which blocks in an
    uninterruptible idle-timeout loop and only exits via that timeout or an
    uncaught `KeyboardInterrupt`), `run_until` blocks on a
    `threading.Event` so a caller can request a clean shutdown at any time
    — needed for SIGINT/SIGTERM handling and for reconnect-with-backoff in
    `cli.py`.
    """

    def __init__(
        self,
        on_subscribe_result: Callable[[dict[str, Any]], None],
        on_feed_message: Callable[[str, Any], object],
        *,
        topics: Sequence[str] | None = None,
        no_auth: bool = False,
        idle_timeout: float = 60.0,
    ) -> None:
        super().__init__(filename=os.devnull, filemode="w", timeout=0, no_auth=no_auth)
        if topics is not None:
            self.topics = list(topics)
        self._on_subscribe_result = on_subscribe_result
        self._on_feed_message = on_feed_message
        self._idle_timeout = idle_timeout

    def run_until(self, stop_event: threading.Event) -> None:
        """Connect, subscribe, and block until `stop_event` is set or the
        feed goes idle for longer than `idle_timeout` seconds (0 disables
        the idle check).
        """
        self._run()
        self._t_last_message = time.time()
        try:
            while not stop_event.is_set():
                idle_for = time.time() - (self._t_last_message or 0.0)
                if self._idle_timeout and idle_for > self._idle_timeout:
                    logger.warning("No data for more than %ss; reconnecting", self._idle_timeout)
                    return
                stop_event.wait(timeout=1.0)
        finally:
            self._exit()

    def _on_message(self, msg: list[Any] | CompletionMessage) -> None:
        self._t_last_message = time.time()
        if isinstance(msg, CompletionMessage):
            self._on_subscribe_result(dict(msg.result))
        else:
            if len(msg) < 2:
                logger.warning("Ignoring malformed feed message: %r", msg)
                return
            self._on_feed_message(msg[0], msg[1])


def check_live(*, no_auth: bool = False, connect_timeout: float = 15.0) -> dict[str, Any]:
    """Connect briefly and return whatever the initial subscribe snapshot is.

    Used by `--dry-run`: an empty (or `SessionInfo`-less) snapshot means no
    session is currently live, which is the expected, non-error outcome
    outside a race weekend.
    """
    result: dict[str, Any] = {}
    got_result = threading.Event()

    def on_subscribe_result(snapshot: dict[str, Any]) -> None:
        result.update(snapshot)
        got_result.set()

    client = LiveFeedClient(
        on_subscribe_result=on_subscribe_result,
        on_feed_message=lambda _topic, _payload: None,
        topics=default_topics(),
        no_auth=no_auth,
        idle_timeout=0,
    )
    stop_event = threading.Event()

    def run_client() -> None:
        try:
            client.run_until(stop_event)
        except Exception:
            # A failed connection attempt is a "not live" signal for
            # --dry-run purposes, not a crash — see the module docstring.
            logger.warning("Live feed connection attempt failed", exc_info=True)
        finally:
            got_result.set()

    thread = threading.Thread(target=run_client, daemon=True)
    thread.start()
    got_result.wait(timeout=connect_timeout)
    stop_event.set()
    thread.join(timeout=connect_timeout)
    return result
