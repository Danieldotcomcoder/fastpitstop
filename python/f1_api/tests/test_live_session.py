"""A session in the store is not a session in progress.

`provenance == "live"` only ever meant "`StoreSource` is serving this" — and
since every ingested session stays in the log forever, it was permanently
true. Three surfaces read it as "happening now":

- `/sessions` reported the 2026 Spanish GP as live for the whole week after
  it finished, so `/live` rendered a six-day-old race as the running one;
- `RaceWorkspace` therefore opened `mode=live` against it;
- `_stream_live` then walked all 33k of its events through the kernel and
  narrated its anomalies at the LLM, once per connected tab — which is what
  pinned the API (a `/degradation` call measured at 176 seconds) and spent
  OpenRouter credit with nobody watching a race.

These pin the distinction at the two layers that decide it.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import f1_api.sessions as sessions_module
from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.sessions import SessionEntry, list_sessions, resolve_session
from f1_api.stream.live import LIVE_HUBS
from f1_api.why import reset_narration_model
from f1_domain import DomainEvent, LapCompleted, SessionKey, SessionType
from f1_kernel import MemorySource, SessionRef

FINISHED = SessionKey(year=2026, round=14, session_type=SessionType.RACE)
RUNNING = SessionKey(year=2026, round=15, session_type=SessionType.RACE)


def _events(key: SessionKey) -> list[DomainEvent]:
    return [
        LapCompleted(
            session_key=key,
            ts_utc=datetime(2026, 9, 13, 14, 0, tzinfo=UTC) + timedelta(seconds=i),
            source_seq=i,
            driver="1",
            lap_number=i,
        )
        for i in range(1, 4)
    ]


class _FakeStoreSource(MemorySource):
    """`MemorySource` with a `sessions()` that names both keys, standing in
    for the real `StoreSource` without needing a database."""

    def sessions(self) -> list[SessionRef]:
        return [
            SessionRef(session_key=FINISHED, event_name="Spanish Grand Prix"),
            SessionRef(session_key=RUNNING, event_name="Italian Grand Prix"),
        ]


@pytest.fixture
def store_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """A configured store where round 14 has finished and round 15 is
    running — the exact shape production was in."""
    source = _FakeStoreSource(_events(FINISHED) + _events(RUNNING))

    # Reaching for the private `_Store` deliberately: it is the seam that
    # carries liveness out of the one place holding an open connection, and
    # standing it up honestly is the only way to test these two functions
    # without a database.
    @contextmanager
    def fake_store(
        _: Settings,
    ) -> Generator[sessions_module._Store | None, None, None]:  # pyright: ignore[reportPrivateUsage]
        yield sessions_module._Store(  # pyright: ignore[reportPrivateUsage]
            source=source, live_keys=frozenset({RUNNING})
        )

    monkeypatch.setattr(sessions_module, "_store_source", fake_store)
    return settings


def test_a_finished_session_in_the_store_is_not_live(store_settings: Settings) -> None:
    """The regression: being in Postgres is not being in progress."""
    by_key = {entry.session_key: entry for entry in list_sessions(store_settings)}

    finished = by_key[FINISHED]
    assert finished.provenance == "live", "the store is still the source serving it"
    assert finished.live is False, "but the race is over"

    assert by_key[RUNNING].live is True


def test_resolve_session_carries_liveness_too(store_settings: Settings) -> None:
    """`/stream` resolves rather than lists, so the flag has to survive both
    paths or the WebSocket guard below reads a default `False` for a race
    that really is running."""
    with resolve_session(store_settings, FINISHED) as resolved:
        assert resolved is not None
        assert resolved.entry.live is False
    with resolve_session(store_settings, RUNNING) as resolved:
        assert resolved is not None
        assert resolved.entry.live is True


def test_the_sessions_route_publishes_the_distinction(store_settings: Settings) -> None:
    """The frontend picks the live session off this payload, so the two
    fields have to be separately visible in it."""
    client = TestClient(create_app(store_settings))
    rows = {row["key"]: row for row in client.get("/sessions").json()}

    assert rows["2026-14-race"]["live"] is False
    assert rows["2026-14-race"]["live_recorded"] is True, "it was still a live ingest"
    assert rows["2026-15-race"]["live"] is True


def test_mode_live_is_refused_for_a_finished_session(store_settings: Settings) -> None:
    """Defence in depth: even a hand-typed URL must not start a narration
    loop over a race that has already happened."""
    client = TestClient(create_app(store_settings))
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect("/stream/2026-14-race?mode=live") as ws,
    ):
        ws.receive_json()
    assert excinfo.value.code == 4404
    assert "currently running" in excinfo.value.reason


def test_mode_live_still_works_for_a_session_that_is_running(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half, and the one a guard like this usually breaks: a race
    that IS running must still stream."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reset_narration_model()

    client = TestClient(create_app(store_settings))
    try:
        with client.websocket_connect("/stream/2026-15-race?mode=live") as ws:
            envelope = ws.receive_json()
        assert envelope["kind"] == "lap_completed"
    finally:
        # The process-wide hub outlives its viewer for a grace period; a
        # later test must not inherit this one's fake store through it.
        LIVE_HUBS.stop_all()
        reset_narration_model()


def test_historical_sessions_are_never_live(settings: Settings) -> None:
    """No store configured at all: the checked-in fixture is backfill, and
    nothing about it may read as in progress."""
    entries = list_sessions(settings)
    assert entries, "the checked-in historical fixture should list something"
    assert all(entry.provenance == "historical" for entry in entries)
    assert not any(entry.live for entry in entries)


def test_live_defaults_to_false() -> None:
    """A `SessionEntry` built without the flag is not live. The default
    matters: every construction site that forgets it should fail closed, the
    direction that costs a page rather than a bill."""
    assert SessionEntry(session_key=FINISHED, event_name=None, provenance="live").live is False


def test_a_failed_liveness_query_leaves_the_connection_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Nothing is live" is the documented fallback when the liveness query
    fails, but the failure also aborted the transaction: every later read on
    that connection failed too, so `/sessions` (the `/live` page's own poll)
    answered 500 and the page said the API was unreachable."""
    import f1_kernel
    import f1_store

    class FakeConnection:
        def __init__(self) -> None:
            self.rolled_back = False
            self.closed = False

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    conn = FakeConnection()

    def failing_query(_: object) -> set[SessionKey]:
        raise RuntimeError('relation "events" does not exist')

    def fake_connect(_dsn: object) -> FakeConnection:
        return conn

    def fake_source(_conn: object) -> MemorySource:
        return MemorySource([])

    monkeypatch.setattr(f1_store, "connect", fake_connect)
    monkeypatch.setattr(f1_store, "live_session_keys", failing_query)
    monkeypatch.setattr(f1_kernel, "StoreSource", fake_source)

    settings = Settings(store_dsn="postgresql://unused")
    with sessions_module._store_source(settings) as store:  # pyright: ignore[reportPrivateUsage]
        assert store is not None
        assert store.live_keys == frozenset()
        assert conn.rolled_back, "the aborted transaction must be cleared before reuse"
    assert conn.closed
