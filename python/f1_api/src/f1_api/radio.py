"""The seam for Phase 4.4: resolving the team-radio corpus (`radio_transcripts`,
via `f1_radio`) the same way `f1_api.sessions` resolves the event log/kernel.

Unlike `f1_kernel.HistoricalSource`, the radio corpus has no DB-free backing
at all — a transcript only ever lives in Postgres (CLAUDE.md's Phase 4
invariant: transcripts + embeddings are a store keyed to the `TeamRadioClip`
event, not something `f1_backfill`'s parquet carries). So, unlike
`f1_api.sessions._store_source` (a plain function `resolve_session` calls
directly — testable because `HistoricalSource` needs no database),
`get_radio_search_index`/`get_radio_store` here are real FastAPI `Depends`
providers: the injection seam tests need to swap in a `Memory*` corpus, the
same lever `main.py` already uses for `get_settings` in `create_app`.

`get_embedding_backend` is the other injectable seam — a process-level
lazy singleton in production (loading a real `sentence-transformers` model
once, never per-request, per `f1_radio.index_batch`'s own "load once, reuse"
precedent) that tests override with a fake, so `/radio/search` never
downloads a real model in CI.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from typing import Annotated
from urllib.parse import quote, unquote

import psycopg
from fastapi import Depends
from psycopg.rows import TupleRow

from f1_api.config import Settings, get_settings
from f1_api.radio_fixture import FixtureEmbeddingBackend, load_radio_fixture
from f1_api.sessions import parse_session_key, session_key_path
from f1_domain import SessionKey
from f1_radio.embeddings import DEFAULT_EMBEDDING_MODEL, EmbeddingBackend, load_embedding_model
from f1_radio.search import MemorySearchIndex, PostgresSearchIndex, SearchIndex
from f1_radio.store import MemoryTranscriptStore, PostgresTranscriptStore, TranscriptStore


def clip_id_path(session_key: SessionKey, driver: str | None, captured_at: datetime | None) -> str:
    """The `{clip_id}` a client puts in `/radio/{clip_id}` — round-trips
    through `parse_clip_id`. Each component is percent-encoded on its own
    (`driver`/`captured_at`'s ISO form both contain characters — `:`, `.`,
    `+` — that would otherwise collide with a hand-picked delimiter) then
    joined with `~`, a character `quote` never produces."""
    session_part = quote(session_key_path(session_key), safe="")
    driver_part = quote(driver, safe="") if driver is not None else ""
    captured_part = quote(captured_at.isoformat(), safe="") if captured_at is not None else ""
    return f"{session_part}~{driver_part}~{captured_part}"


def parse_clip_id(clip_id: str) -> tuple[SessionKey, str | None, datetime | None]:
    """Inverse of `clip_id_path`. Raises `ValueError` on anything malformed
    — the route turns that into a 422, never a 500."""
    parts = clip_id.split("~")
    if len(parts) != 3:
        raise ValueError(
            f"malformed clip id {clip_id!r}, expected '<session>~<driver>~<captured_at>'"
        )
    session_part, driver_part, captured_part = parts
    session_key = parse_session_key(unquote(session_part))
    driver = unquote(driver_part) if driver_part else None
    captured_at = datetime.fromisoformat(unquote(captured_part)) if captured_part else None
    return session_key, driver, captured_at


@contextmanager
def _radio_connection(
    settings: Settings,
) -> Generator[psycopg.Connection[TupleRow] | None, None, None]:
    """The live `radio_transcripts` connection, or `None` when no DSN is
    configured or the database is unreachable — mirrors
    `f1_api.sessions._store_source`'s exact degrade-gracefully discipline,
    just against the same `F1_STORE_DSN` for a different table."""
    if not settings.store_dsn:
        yield None
        return
    try:
        from f1_store import connect
    except ImportError:  # pragma: no cover - f1_store is a hard dependency
        yield None
        return
    try:
        conn = connect(settings.store_dsn)
    except Exception:
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def radio_store_cm(settings: Settings) -> Generator[TranscriptStore | None, None, None]:
    """The plain-function form (mirrors `f1_api.sessions._store_source`'s
    "call it directly with a `with`" shape) — used by the `/stream/{key}`
    WebSocket route, which resolves its own dependencies outside FastAPI's
    `Depends` machinery. `get_radio_store` below is the `Depends`-wrapped
    form the REST routes use instead, so tests can override it.

    `settings.radio_fixture_path` set (only by `just verify-phase4`'s
    Playwright harness — see `f1_api.radio_fixture`) short-circuits straight
    to a `MemoryTranscriptStore` seeded from the checked-in fixture, never
    touching `F1_STORE_DSN` at all."""
    if settings.radio_fixture_path is not None:
        store = MemoryTranscriptStore()
        for record in load_radio_fixture(settings.radio_fixture_path).records:
            store.insert(record)
        yield store
        return
    with _radio_connection(settings) as conn:
        yield PostgresTranscriptStore(conn) if conn is not None else None


@contextmanager
def radio_search_index_cm(settings: Settings) -> Generator[SearchIndex | None, None, None]:
    """The plain-function form (mirrors `radio_store_cm`) — used by the
    `/stream/{key}` WebSocket and Phase 5.4's why-engine seam, which resolve
    their own dependencies outside FastAPI's `Depends` machinery.
    `get_radio_search_index` below is the `Depends`-wrapped form the REST
    routes use instead, so tests can override it."""
    if settings.radio_fixture_path is not None:
        yield MemorySearchIndex(load_radio_fixture(settings.radio_fixture_path).records)
        return
    with _radio_connection(settings) as conn:
        yield PostgresSearchIndex(conn) if conn is not None else None


def get_radio_search_index(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[SearchIndex | None, None, None]:
    """`None` means the corpus is unavailable (no `F1_STORE_DSN`, or
    unreachable) — routes degrade to an empty result, never a 500. Overridden
    in tests (`app.dependency_overrides`) with a `MemorySearchIndex` seeded
    with planted `TranscriptRecord`s, the same lever `main.py` already uses
    for `get_settings`, since (unlike historical sessions) there is no
    DB-free real corpus this package could test against instead.

    `settings.radio_fixture_path` set makes this yield a `MemorySearchIndex`
    seeded from the fixture instead — the same DB-free path a real,
    externally-driven process (Playwright, not a pytest override) can reach
    via the `F1_API_RADIO_FIXTURE` env var."""
    with radio_search_index_cm(settings) as index:
        yield index


def get_radio_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[TranscriptStore | None, None, None]:
    """`None` under the same conditions as `get_radio_search_index` — the
    per-clip counterpart used by `/sessions/{key}/radio` and `/radio/{clip_id}`."""
    with radio_store_cm(settings) as store:
        yield store


SearchIndexDep = Annotated[SearchIndex | None, Depends(get_radio_search_index)]
RadioStoreDep = Annotated[TranscriptStore | None, Depends(get_radio_store)]

_embedding_backend: EmbeddingBackend | None = None
_embedding_backend_lock = Lock()


def get_embedding_backend() -> EmbeddingBackend:
    """A process-level lazy singleton: the real local `sentence-transformers`
    model loads at most once per process — never per request (loading one is
    the expensive part, per `f1_radio.embeddings`' own docs)."""
    global _embedding_backend
    if _embedding_backend is None:
        with _embedding_backend_lock:
            if _embedding_backend is None:
                _embedding_backend = load_embedding_model(DEFAULT_EMBEDDING_MODEL)
    return _embedding_backend


def get_embedding_backend_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Callable[[], EmbeddingBackend]:
    """A dependency resolving to `get_embedding_backend` itself, UNCALLED —
    resolving this is always cheap (it never loads the model), so `/radio/
    search` can depend on it unconditionally and only invoke the factory
    when a query is actually non-blank AND a corpus is configured. Without
    this indirection, requiring `EmbeddingBackend` directly as a dependency
    would make FastAPI resolve (and therefore load) a real model on EVERY
    request, including a blank-query search against an unconfigured corpus
    — exactly the "never per request" guarantee above would otherwise
    silently violate. Overridden in tests with a factory returning a fake.

    `settings.radio_fixture_path` set resolves instead to a factory
    returning a `FixtureEmbeddingBackend` — see `f1_api.radio_fixture`'s
    module docstring for why a real running process needs this same
    no-real-model escape hatch a pytest override already has."""
    if settings.radio_fixture_path is not None:
        fixture = load_radio_fixture(settings.radio_fixture_path)
        backend = FixtureEmbeddingBackend(fixture.query_embeddings, fixture.default_query_embedding)
        return lambda: backend
    return get_embedding_backend


EmbeddingBackendFactoryDep = Annotated[
    Callable[[], EmbeddingBackend], Depends(get_embedding_backend_factory)
]


__all__ = [
    "EmbeddingBackendFactoryDep",
    "RadioStoreDep",
    "SearchIndexDep",
    "clip_id_path",
    "get_embedding_backend",
    "get_embedding_backend_factory",
    "get_radio_search_index",
    "get_radio_store",
    "parse_clip_id",
    "radio_search_index_cm",
    "radio_store_cm",
]
