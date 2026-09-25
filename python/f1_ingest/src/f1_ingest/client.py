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

import fastf1.livetiming.client as _fastf1_livetiming_client
from fastf1.livetiming.client import SignalRClient
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.messages.completion_message import CompletionMessage

from f1_ingest.topics import default_topics

logger = logging.getLogger(__name__)


class LiveFeedConnectionError(RuntimeError):
    """The connection attempt itself failed.

    Distinct from "connected fine, but no session is live" — conflating the
    two is what makes a broken client look like a quiet weekend.
    """


class _NoAuthCompatHubConnectionBuilder(HubConnectionBuilder):
    """Drops a present-but-`None` `access_token_factory` before delegating.

    FastF1 signals "no auth" by passing the key with a `None` value::

        "access_token_factory": None if self._no_auth else get_auth_token

    signalrcore < 1.0 ignored that. signalrcore >= 1.0 raises
    `TypeError("access_token_factory is not function")` for any present key
    that is not callable, so FastF1's own `no_auth=True` path cannot connect
    at all. This package pins `signalrcore>=0.9` with no upper bound, so a
    routine dependency resolve picks up 1.x and the entire unauthenticated
    recording path breaks — discoverable only during a live session, which
    is exactly when there is no time to debug it.

    Omitting the key entirely is what "no auth" was always meant to be:
    signalrcore only attaches an `Authorization` header when `auth_function`
    is set. Kept as a narrow shim here rather than an upper-bound pin so we
    stay on a maintained signalrcore, and scoped to our own `_run` so no
    other FastF1 caller in-process is affected.
    """

    def with_url(self, hub_url: str, options: dict[str, Any] | None = None) -> Any:
        if options is not None and options.get("access_token_factory", False) is None:
            options = {k: v for k, v in options.items() if k != "access_token_factory"}
        # signalrcore ships no type information for the builder this
        # overrides, so pyright can only see the base method as untyped.
        return super().with_url(hub_url, options=options)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]


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

    def _run(self) -> None:
        """FastF1's `_run`, with the signalrcore>=1.0 no-auth incompatibility
        patched out for the duration of the call only."""
        original = _fastf1_livetiming_client.HubConnectionBuilder
        _fastf1_livetiming_client.HubConnectionBuilder = _NoAuthCompatHubConnectionBuilder
        try:
            super()._run()
        finally:
            _fastf1_livetiming_client.HubConnectionBuilder = original

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

    Raises `LiveFeedConnectionError` if the connection attempt itself failed.
    That is NOT the same as "no session live", and reporting it as such
    (which this function used to do) hides a broken client behind a message
    that looks like a quiet weekend — the failure mode is invisible for
    months and then costs you the one session you cared about.
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

    failure: list[BaseException] = []

    def run_client() -> None:
        try:
            client.run_until(stop_event)
        except Exception as exc:
            logger.warning("Live feed connection attempt failed", exc_info=True)
            failure.append(exc)
        finally:
            got_result.set()

    thread = threading.Thread(target=run_client, daemon=True)
    thread.start()
    got_result.wait(timeout=connect_timeout)
    stop_event.set()
    thread.join(timeout=connect_timeout)
    if failure and not result:
        raise LiveFeedConnectionError(
            f"could not connect to the live feed: {failure[0]!r}"
        ) from failure[0]
    return result
