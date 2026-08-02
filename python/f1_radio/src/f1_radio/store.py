"""Data access for `radio_transcripts` -- Phase 4's transcript store.
Reuses `f1_store`'s own DSN/connection config and migration mechanism rather
than inventing a second one: the schema itself lives at
`f1_store/migrations/002_radio_transcripts.sql` (base columns) and
`003_radio_embeddings.sql` (Phase 4.3's embedding + race-context columns),
applied by the existing `f1-store-migrate` CLI alongside Phase 1's own
tables. It is a genuinely SEPARATE table from `team_radio` (CLAUDE.md's
Phase 4 invariant) -- nothing here participates in `f1_store`'s
DomainEvent-derived sink/rows machinery, since a transcript is not itself a
domain event.

`insert_transcript`/`get_transcript` are the low-level, connection-taking
functions the DB-gated tests (`tests/test_migrations.py`) exercise directly.
`TranscriptStore` wraps them behind a small protocol -- `PostgresTranscriptStore`
for real use, `MemoryTranscriptStore` for tests and dry runs -- the same "the
DB is a sink, fully testable with no database present" split `f1_store`
itself makes with `PostgresSink`/`MemorySink`. `f1_radio.transcribe_batch`
(the offline batch runner, Phase 4.2) is written against `TranscriptStore`
only, so its idempotency/resumability/parallel-worker logic has a real unit
test suite that needs no TimescaleDB. `f1_radio.index_batch` (4.3) and
`f1_radio.search` (4.3) use the same split for embeddings.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from f1_domain import SessionKey, SessionType

TABLE = "radio_transcripts"

# Column order matches the CREATE TABLE in
# f1_store/migrations/002_radio_transcripts.sql exactly.
TRANSCRIPT_COLUMNS: tuple[str, ...] = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "captured_at",
    "audio_path",
    "transcript_text",
    "language",
    "confidence",
    "model_version",
    "segments",
)

# Phase 4.3's columns, added by migration 003 -- always NULL until
# `TranscriptStore.set_embedding` (`f1_radio.index_batch`) backfills them,
# since a clip is transcribed (4.2) well before it is indexed (4.3).
# Separate from TRANSCRIPT_COLUMNS (rather than folded in) so
# `insert_transcript`'s column/value zip -- which only ever supplies the
# base fields -- does not have to change shape.
EMBEDDING_COLUMNS: tuple[str, ...] = (
    "embedding",
    "embedding_model_version",
    "lap_number",
    "position",
    "track_status",
    "track_status_flags",
)

# What get_transcript/list_transcripts_for_season/f1_radio.search select --
# every column this module knows about, base + embedding, so a caller never
# has to guess which subset a given row was built from.
ALL_COLUMNS: tuple[str, ...] = TRANSCRIPT_COLUMNS + EMBEDDING_COLUMNS

# Matches the SQL UNIQUE constraint (radio_transcripts_clip_unique) -- the
# key that guarantees a clip is transcribed at most once.
IDENTITY_KEY: tuple[str, ...] = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "captured_at",
    "audio_path",
)


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    session_key: SessionKey
    driver: str | None
    captured_at: datetime | None
    audio_path: str
    transcript_text: str | None
    language: str | None
    confidence: float | None
    model_version: str
    segments: list[dict[str, Any]] | None = None
    # Phase 4.3 -- always None until `TranscriptStore.set_embedding` backfills
    # them (see EMBEDDING_COLUMNS above).
    embedding: list[float] | None = None
    embedding_model_version: str | None = None
    lap_number: int | None = None
    position: int | None = None
    track_status: str | None = None
    track_status_flags: list[str] | None = None


def insert_transcript(conn: psycopg.Connection[TupleRow], record: TranscriptRecord) -> bool:
    """Insert one transcript row. Returns False without writing anything if
    this clip already has a transcript (`ON CONFLICT DO NOTHING` against the
    `radio_transcripts_clip_unique` constraint) -- a clip is transcribed at
    MOST once, and a re-run never crashes on the duplicate.
    """
    col_ident = sql.SQL(", ").join(sql.Identifier(c) for c in TRANSCRIPT_COLUMNS)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in TRANSCRIPT_COLUMNS)
    stmt = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT ON CONSTRAINT radio_transcripts_clip_unique DO NOTHING "
        "RETURNING session_year"
    ).format(table=sql.Identifier(TABLE), cols=col_ident, placeholders=placeholders)
    with conn.cursor() as cur:
        cur.execute(
            stmt,
            (
                record.session_key.year,
                record.session_key.round,
                record.session_key.session_type.value,
                record.driver,
                record.captured_at,
                record.audio_path,
                record.transcript_text,
                record.language,
                record.confidence,
                record.model_version,
                Jsonb(record.segments) if record.segments is not None else None,
            ),
        )
        inserted = cur.fetchone() is not None
    conn.commit()
    return inserted


def get_transcript(
    conn: psycopg.Connection[TupleRow],
    session_key: SessionKey,
    driver: str | None,
    captured_at: datetime | None,
) -> TranscriptRecord | None:
    """The transcript for one clip (base fields + any Phase 4.3 embedding/
    context columns already backfilled), if one has been written yet."""
    col_ident = sql.SQL(", ").join(sql.Identifier(c) for c in ALL_COLUMNS)
    stmt = sql.SQL(
        "SELECT {cols} FROM {table} "
        "WHERE session_year = %s AND session_round = %s AND session_type = %s "
        "AND driver IS NOT DISTINCT FROM %s AND captured_at IS NOT DISTINCT FROM %s"
    ).format(cols=col_ident, table=sql.Identifier(TABLE))
    with conn.cursor() as cur:
        cur.execute(
            stmt,
            (
                session_key.year,
                session_key.round,
                session_key.session_type.value,
                driver,
                captured_at,
            ),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return record_from_row(row)


def list_transcripts_for_season(
    conn: psycopg.Connection[TupleRow], season: int, *, round_filter: int | None = None
) -> list[TranscriptRecord]:
    """Every transcript row for `season` (optionally one `round_filter`),
    in no particular order -- the loader half of the quality report
    (`f1_radio.quality_report`), which is a pure function over whatever list
    of records it's handed."""
    col_ident = sql.SQL(", ").join(sql.Identifier(c) for c in ALL_COLUMNS)
    conditions = [sql.SQL("session_year = %s")]
    params: list[Any] = [season]
    if round_filter is not None:
        conditions.append(sql.SQL("session_round = %s"))
        params.append(round_filter)
    stmt = sql.SQL("SELECT {cols} FROM {table} WHERE {where}").format(
        cols=col_ident, table=sql.Identifier(TABLE), where=sql.SQL(" AND ").join(conditions)
    )
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        rows = cur.fetchall()
    return [record_from_row(row) for row in rows]


def update_transcript_context(
    conn: psycopg.Connection[TupleRow],
    session_key: SessionKey,
    driver: str | None,
    captured_at: datetime | None,
    *,
    embedding: list[float],
    embedding_model_version: str,
    lap_number: int | None,
    position: int | None,
    track_status: str | None,
    track_status_flags: list[str] | None,
) -> bool:
    """Backfills one already-transcribed clip's embedding + race-context
    columns (Phase 4.3). Returns False without writing anything if no
    transcript row exists yet for this clip -- a clip must be TRANSCRIBED
    (4.2) before it can be INDEXED (4.3), the same "skip what isn't ready
    yet" discipline `run_batch` applies to un-fetched audio.
    """
    stmt = sql.SQL(
        "UPDATE {table} SET embedding = %s, embedding_model_version = %s, "
        "lap_number = %s, position = %s, track_status = %s, track_status_flags = %s "
        "WHERE session_year = %s AND session_round = %s AND session_type = %s "
        "AND driver IS NOT DISTINCT FROM %s AND captured_at IS NOT DISTINCT FROM %s"
    ).format(table=sql.Identifier(TABLE))
    with conn.cursor() as cur:
        cur.execute(
            stmt,
            (
                vector_literal(embedding),
                embedding_model_version,
                lap_number,
                position,
                track_status,
                track_status_flags,
                session_key.year,
                session_key.round,
                session_key.session_type.value,
                driver,
                captured_at,
            ),
        )
        updated = cur.rowcount > 0
    conn.commit()
    return updated


def vector_literal(values: list[float]) -> str:
    """pgvector's own text input format (`'[0.1,-0.2,...]'`). Written by
    hand rather than pulling in the `pgvector` package's psycopg adapter:
    Postgres resolves an untyped text parameter against the target
    column's type on INSERT/UPDATE, so a plain string round-trips through
    psycopg with no custom type registration needed on either side."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _parse_vector(text: str | None) -> list[float] | None:
    """The inverse of `vector_literal` -- psycopg has no adapter for the
    `vector` type either, so a SELECTed column comes back as this same
    text representation, not a Python list."""
    if text is None:
        return None
    inner = text.strip().strip("[]")
    if not inner:
        return []
    return [float(x) for x in inner.split(",")]


def record_from_row(row: tuple[Any, ...]) -> TranscriptRecord:
    """Builds a `TranscriptRecord` from one row selected as `ALL_COLUMNS`
    (base + Phase 4.3 embedding/context) -- shared by `get_transcript`,
    `list_transcripts_for_season` and `f1_radio.search.PostgresSearchIndex`,
    so there is exactly one place that knows this column order."""
    (
        year,
        round_no,
        session_type,
        row_driver,
        row_captured_at,
        audio_path,
        transcript_text,
        language,
        confidence,
        row_model_version,
        segments,
        embedding,
        embedding_model_version,
        lap_number,
        position,
        track_status,
        track_status_flags,
    ) = row
    return TranscriptRecord(
        session_key=SessionKey(year=year, round=round_no, session_type=SessionType(session_type)),
        driver=row_driver,
        captured_at=row_captured_at,
        audio_path=audio_path,
        transcript_text=transcript_text,
        language=language,
        confidence=confidence,
        model_version=row_model_version,
        segments=segments,
        embedding=_parse_vector(embedding),
        embedding_model_version=embedding_model_version,
        lap_number=lap_number,
        position=position,
        track_status=track_status,
        track_status_flags=list(track_status_flags) if track_status_flags is not None else None,
    )


class TranscriptStore(Protocol):
    """What `f1_radio.transcribe_batch.run_batch` (and, for `set_embedding`,
    `f1_radio.index_batch.run_index_batch`) needs from a transcript store --
    narrow enough that a real Postgres-backed implementation and an
    in-memory fake both satisfy it with no shared base class, the same
    `EventSink`-style protocol split `f1_store` uses for its own sinks."""

    def get(
        self, session_key: SessionKey, driver: str | None, captured_at: datetime | None
    ) -> TranscriptRecord | None: ...

    def insert(self, record: TranscriptRecord) -> bool: ...

    def set_embedding(
        self,
        session_key: SessionKey,
        driver: str | None,
        captured_at: datetime | None,
        *,
        embedding: list[float],
        embedding_model_version: str,
        lap_number: int | None,
        position: int | None,
        track_status: str | None,
        track_status_flags: list[str] | None,
    ) -> bool: ...

    def close(self) -> None: ...


class PostgresTranscriptStore:
    """Wraps one `psycopg` connection around the module-level `insert_transcript`/
    `get_transcript`/`update_transcript_context` functions. One instance per
    connection -- `close()` closes it. `run_batch`/`run_index_batch` open one
    of these per clip they write rather than holding a single shared
    connection across worker threads, since psycopg connections are not safe
    to use concurrently from multiple threads and a season's clip count (low
    hundreds/thousands) makes per-write connection overhead a non-issue.
    """

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def get(
        self, session_key: SessionKey, driver: str | None, captured_at: datetime | None
    ) -> TranscriptRecord | None:
        return get_transcript(self._conn, session_key, driver, captured_at)

    def insert(self, record: TranscriptRecord) -> bool:
        return insert_transcript(self._conn, record)

    def set_embedding(
        self,
        session_key: SessionKey,
        driver: str | None,
        captured_at: datetime | None,
        *,
        embedding: list[float],
        embedding_model_version: str,
        lap_number: int | None,
        position: int | None,
        track_status: str | None,
        track_status_flags: list[str] | None,
    ) -> bool:
        return update_transcript_context(
            self._conn,
            session_key,
            driver,
            captured_at,
            embedding=embedding,
            embedding_model_version=embedding_model_version,
            lap_number=lap_number,
            position=position,
            track_status=track_status,
            track_status_flags=track_status_flags,
        )

    def close(self) -> None:
        self._conn.close()


class MemoryTranscriptStore:
    """An in-memory `TranscriptStore` -- for tests and dry runs, needing no
    database, mirroring `f1_store.sink.MemorySink`. Keyed on
    `(session, driver, captured_at)`, matching `get_transcript`'s own lookup
    (not the DB's full `IDENTITY_KEY`, which also includes `audio_path` --
    that extra column only matters to the UNIQUE CONSTRAINT itself, never to
    an existence check). Thread-safe (a lock around the shared dict) so a
    multi-worker `run_batch` test can assert the no-double-write guarantee
    deterministically, the same guarantee the real unique constraint gives
    for free against Postgres.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[Any, ...], TranscriptRecord] = {}
        self._lock = threading.Lock()

    def _key(
        self, session_key: SessionKey, driver: str | None, captured_at: datetime | None
    ) -> tuple[Any, ...]:
        return (
            session_key.year,
            session_key.round,
            session_key.session_type.value,
            driver,
            captured_at,
        )

    def get(
        self, session_key: SessionKey, driver: str | None, captured_at: datetime | None
    ) -> TranscriptRecord | None:
        with self._lock:
            return self._records.get(self._key(session_key, driver, captured_at))

    def insert(self, record: TranscriptRecord) -> bool:
        key = self._key(record.session_key, record.driver, record.captured_at)
        with self._lock:
            if key in self._records:
                return False
            self._records[key] = record
            return True

    def set_embedding(
        self,
        session_key: SessionKey,
        driver: str | None,
        captured_at: datetime | None,
        *,
        embedding: list[float],
        embedding_model_version: str,
        lap_number: int | None,
        position: int | None,
        track_status: str | None,
        track_status_flags: list[str] | None,
    ) -> bool:
        key = self._key(session_key, driver, captured_at)
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                return False
            self._records[key] = replace(
                existing,
                embedding=embedding,
                embedding_model_version=embedding_model_version,
                lap_number=lap_number,
                position=position,
                track_status=track_status,
                track_status_flags=track_status_flags,
            )
            return True

    def close(self) -> None:
        pass

    def all(self) -> list[TranscriptRecord]:
        with self._lock:
            return list(self._records.values())
