"""Resolving a `SessionKey` to data: this is the seam that lets every route
below stay agnostic about where a session actually lives.

Two backing sources, exactly `f1_kernel`'s own two non-memory `EventSource`s:

- **historical** — `f1_backfill` parquet via `HistoricalSource`. Always
  available (a machine with nothing backfilled just lists zero sessions,
  per `f1_kernel.list_historical_sessions`'s own "empty, not an error"
  discipline).
- **live** — the TimescaleDB event log via `StoreSource`, when
  `F1_STORE_DSN` is configured and reachable. Optional: CLAUDE.md's "the DB
  is a sink, never a dependency" extends here too — no database configured,
  or unreachable, degrades to "no live sessions" rather than a 500.

A session present in both is reported with `provenance="live"`: the store is
the source of truth for a session currently being ingested, and it can be
queried while a historical adapter run for the same event would only ever see
the final backfill.

`provenance` and `live` are DIFFERENT questions, and conflating them was a
real outage. `provenance` says which source serves this session; `live` says
whether it is happening right now. Every session ever ingested stays in the
store forever, so `provenance == "live"` is permanent, while `live` is
`f1_store.live_session_keys`' measured answer and goes false once the event
log stops growing.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from f1_api.config import Settings
from f1_domain import SessionKey, SessionType
from f1_kernel import EventSource, HistoricalSource

_log = logging.getLogger(__name__)

Provenance = Literal["live", "historical"]


def session_key_path(session_key: SessionKey) -> str:
    """The `{key}` a client puts in `/sessions/{key}/...` — round-trips
    through `parse_session_key`. `-`-joined rather than `_`-joined (the
    historical catalog's own convention) because `SessionType` values
    themselves contain underscores (`sprint_qualifying`); `-` never appears
    in a session type, so splitting on it is unambiguous."""
    return f"{session_key.year}-{session_key.round}-{session_key.session_type.value}"


def parse_session_key(key: str) -> SessionKey:
    """Inverse of `session_key_path`. Raises `ValueError` on anything
    malformed — routes turn that into a 422, never a 500."""
    parts = key.split("-", 2)
    if len(parts) != 3:
        raise ValueError(f"malformed session key {key!r}, expected '<year>-<round>-<session_type>'")
    year_str, round_str, type_str = parts
    try:
        year = int(year_str)
        round_number = int(round_str)
        session_type = SessionType(type_str)
    except ValueError as exc:
        raise ValueError(f"malformed session key {key!r}: {exc}") from exc
    return SessionKey(year=year, round=round_number, session_type=session_type)


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One session `/sessions` can list, and what backs it."""

    session_key: SessionKey
    event_name: str | None
    provenance: Provenance
    tables: tuple[str, ...] = ()
    live: bool = False
    """Whether this session is in progress RIGHT NOW — never merely
    whether the store happens to hold it (see the module docstring)."""
    circuit_name: str | None = None
    """The feed's venue short name when the source knows it (live sessions,
    from the log's `session_info_updated`): how a live session finds its
    circuit, since a Grand Prix name alone is ambiguous across seasons."""


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """A session key resolved to the source that can actually serve it."""

    source: EventSource
    entry: SessionEntry


@dataclass(frozen=True, slots=True)
class _Store:
    """The store source plus the one thing only an open connection can
    answer: which of its sessions are actually running."""

    source: EventSource
    live_keys: frozenset[SessionKey]


@contextmanager
def _store_source(settings: Settings) -> Generator[_Store | None, None, None]:
    """The live source, or `None` when no DSN is configured or the database
    is unreachable — both normal, expected states outside a live session
    (see `docs/PHASE0.md`'s "day-to-day development does not run against
    the live feed" — the store mirrors that reality for Phase 3)."""
    if not settings.store_dsn:
        yield None
        return
    try:
        from f1_kernel import StoreSource
        from f1_store import connect, live_session_keys
    except ImportError:  # pragma: no cover - f1_store is a hard dependency
        yield None
        return
    try:
        conn = connect(settings.store_dsn)
    except Exception:
        yield None
        return
    try:
        live_keys: frozenset[SessionKey]
        try:
            live_keys = frozenset(live_session_keys(conn))
        except Exception:
            # An older schema, or a transient query failure. "Nothing is
            # live" is the safe reading: it costs a visitor the live page
            # during a race, where guessing the other way spends money
            # narrating a finished one.
            _log.warning("could not determine live sessions", exc_info=True)
            live_keys = frozenset()
            # The failed query aborted the transaction; without this every
            # later read on the connection fails too, and `/sessions` (the
            # `/live` page's poll) answered 500 instead of degrading.
            conn.rollback()
        yield _Store(source=StoreSource(conn), live_keys=live_keys)
    finally:
        conn.close()


def _sort_key(entry: SessionEntry) -> tuple[int, int, str]:
    key = entry.session_key
    return (key.year, key.round, key.session_type.value)


def list_sessions(settings: Settings) -> list[SessionEntry]:
    """Every session either source can serve, `live` winning over
    `historical` when the same key appears in both (the store is the fresher
    copy of a session currently being ingested)."""
    historical = HistoricalSource(settings.historical_root)
    by_key: dict[SessionKey, SessionEntry] = {
        ref.session_key: SessionEntry(
            session_key=ref.session_key,
            event_name=ref.event_name,
            provenance="historical",
            tables=ref.tables,
        )
        for ref in historical.sessions()
    }
    with _store_source(settings) as store:
        if store is not None:
            for ref in store.source.sessions():
                by_key[ref.session_key] = SessionEntry(
                    session_key=ref.session_key,
                    event_name=ref.event_name,
                    provenance="live",
                    tables=ref.tables,
                    live=ref.session_key in store.live_keys,
                    circuit_name=ref.circuit_name,
                )
    return sorted(by_key.values(), key=_sort_key)


@contextmanager
def resolve_session(
    settings: Settings, session_key: SessionKey
) -> Generator[ResolvedSession | None, None, None]:
    """Find the source backing `session_key`, live first. `None` means
    neither source has it — a 404 to the caller, not a crash.

    A context manager because the live source may hold an open database
    connection for as long as the caller reads from it; the historical
    source holds nothing that needs closing."""
    with _store_source(settings) as store:
        if store is not None:
            for ref in store.source.sessions():
                if ref.session_key == session_key:
                    yield ResolvedSession(
                        source=store.source,
                        entry=SessionEntry(
                            session_key=session_key,
                            event_name=ref.event_name,
                            provenance="live",
                            tables=ref.tables,
                            live=session_key in store.live_keys,
                            circuit_name=ref.circuit_name,
                        ),
                    )
                    return
        historical = HistoricalSource(settings.historical_root)
        for ref in historical.sessions():
            if ref.session_key == session_key:
                yield ResolvedSession(
                    source=historical,
                    entry=SessionEntry(
                        session_key=session_key,
                        event_name=ref.event_name,
                        provenance="historical",
                        tables=ref.tables,
                    ),
                )
                return
        yield None


__all__ = [
    "Provenance",
    "ResolvedSession",
    "SessionEntry",
    "list_sessions",
    "parse_session_key",
    "resolve_session",
    "session_key_path",
]
