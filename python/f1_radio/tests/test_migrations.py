"""DB-backed tests for the `radio_transcripts` schema
(`f1_store/migrations/002_radio_transcripts.sql` plus Phase 4.3's
`003_radio_embeddings.sql`): it applies cleanly alongside Phase 1's own
migration, the clip -> transcript unique constraint actually holds, and
`update_transcript_context` (the embedding/race-context backfill
`TranscriptStore.set_embedding` wraps) round-trips through a real pgvector
column.

Gated (and skipped in CI): needs a real TimescaleDB WITH pgvector (the
`-ha` image in `python/f1_store/docker-compose.yml`, not the plain one --
see that file's own comment for why). Bring one up with
`docker compose -f python/f1_store/docker-compose.yml up -d`, then:

    F1_STORE_RUN_DB_TESTS=1 F1_STORE_TEST_DSN=postgresql://pitwall:pitwall@localhost:5432/pitwall \
        uv run pytest python/f1_radio/tests/test_migrations.py

Reuses f1_store's own env vars and throwaway-database-per-test fixture
pattern (`f1_store/tests/test_postgres_sink.py`) rather than inventing a
second DB-gating convention -- both packages share one Postgres instance in
practice.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from radio_data import KEY

from f1_radio.store import (
    TranscriptRecord,
    get_transcript,
    insert_transcript,
    update_transcript_context,
)
from f1_store import apply_migrations

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB",
)

_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """Create a throwaway database, apply every f1_store migration
    (including 002_radio_transcripts.sql) against it, yield its DSN, drop it
    afterwards."""
    name = f"pitwall_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = _swap_dbname(_ADMIN_DSN, name)
    try:
        with psycopg.connect(dsn) as conn:
            apply_migrations(conn)
        yield dsn
    finally:
        with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def _swap_dbname(dsn: str, dbname: str) -> str:
    base = dsn.rsplit("/", 1)[0]
    return f"{base}/{dbname}"


def _record(**overrides: object) -> TranscriptRecord:
    fields: dict[str, object] = {
        "session_key": KEY,
        "driver": "44",
        "captured_at": datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC),
        "audio_path": "2024/01_race/44_x.mp3",
        "transcript_text": "box this lap, box box",
        "language": "en",
        "confidence": 0.92,
        "model_version": "whisper-test-v1",
        "segments": [{"start": 0.0, "end": 1.2, "text": "box this lap, box box"}],
    }
    fields.update(overrides)
    return TranscriptRecord(**fields)  # type: ignore[arg-type]


def test_schema_applies_from_scratch_and_is_idempotent(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        assert apply_migrations(conn) == []  # already applied by the fixture


def test_insert_then_get_round_trips(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        record = _record()
        assert insert_transcript(conn, record) is True

        fetched = get_transcript(conn, KEY, "44", record.captured_at)
        assert fetched is not None
        assert fetched.transcript_text == record.transcript_text
        assert fetched.language == "en"
        assert fetched.confidence == pytest.approx(0.92)
        assert fetched.model_version == "whisper-test-v1"
        assert fetched.segments == record.segments


def test_insert_transcript_is_idempotent_for_the_same_clip(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        record = _record()
        assert insert_transcript(conn, record) is True
        # Same clip identity, a "re-transcription" attempt -- must be a
        # silent no-op, never a crash, and never a second row.
        again = _record(transcript_text="a different transcript entirely")
        assert insert_transcript(conn, again) is False

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM radio_transcripts")
            row = cur.fetchone()
        assert row is not None
        assert row[0] == 1

        fetched = get_transcript(conn, KEY, "44", record.captured_at)
        assert fetched is not None
        assert fetched.transcript_text == record.transcript_text  # the FIRST insert won


def test_unique_constraint_is_enforced_at_the_database_level(fresh_db: str) -> None:
    """Proves the constraint itself, not just `insert_transcript`'s
    application-level idempotency: two raw INSERTs with no ON CONFLICT
    clause for the same clip identity must have the second one rejected."""
    with psycopg.connect(fresh_db) as conn:
        columns = (
            "session_year, session_round, session_type, driver, captured_at, "
            "audio_path, model_version"
        )
        values = (2024, 1, "race", "44", datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC), "a.mp3", "v1")
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO radio_transcripts ({columns}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                values,
            )
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation), conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO radio_transcripts ({columns}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                values,
            )
        conn.rollback()


def test_get_transcript_returns_none_when_no_row_exists(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        assert get_transcript(conn, KEY, "44", datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)) is None


def test_update_transcript_context_round_trips_the_embedding_and_context(fresh_db: str) -> None:
    """Migration 003's own claim: a pgvector column round-trips through
    `update_transcript_context`/`get_transcript` with no custom psycopg
    type adapter registered on either side (`f1_radio.store.vector_literal`/
    `_parse_vector`)."""
    with psycopg.connect(fresh_db) as conn:
        record = _record()
        assert insert_transcript(conn, record) is True

        updated = update_transcript_context(
            conn,
            KEY,
            "44",
            record.captured_at,
            embedding=[0.1, -0.2, 0.3],
            embedding_model_version="fake-model-v1",
            lap_number=20,
            position=3,
            track_status="green",
            track_status_flags=["yellow"],
        )
        assert updated is True

        fetched = get_transcript(conn, KEY, "44", record.captured_at)
        assert fetched is not None
        assert fetched.embedding is not None
        assert fetched.embedding == pytest.approx([0.1, -0.2, 0.3])
        assert fetched.embedding_model_version == "fake-model-v1"
        assert fetched.lap_number == 20
        assert fetched.position == 3
        assert fetched.track_status == "green"
        assert fetched.track_status_flags == ["yellow"]


def test_update_transcript_context_on_a_clip_with_no_transcript_is_a_no_op(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        updated = update_transcript_context(
            conn,
            KEY,
            "44",
            datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC),
            embedding=[0.1, 0.2, 0.3],
            embedding_model_version="fake-model-v1",
            lap_number=1,
            position=1,
            track_status="green",
            track_status_flags=[],
        )
        assert updated is False


def test_cosine_distance_operator_ranks_the_closer_vector_first(fresh_db: str) -> None:
    """Proves pgvector's `<=>` operator is actually usable against migration
    003's `embedding` column -- the primitive `f1_radio.search.
    PostgresSearchIndex` builds its ORDER BY on."""
    with psycopg.connect(fresh_db) as conn:
        close = _record(audio_path="close.mp3")
        far = _record(audio_path="far.mp3", captured_at=datetime(2024, 3, 2, 15, 5, 0, tzinfo=UTC))
        insert_transcript(conn, close)
        insert_transcript(conn, far)
        update_transcript_context(
            conn,
            KEY,
            "44",
            close.captured_at,
            embedding=[1.0, 0.0, 0.0],
            embedding_model_version="v1",
            lap_number=None,
            position=None,
            track_status=None,
            track_status_flags=None,
        )
        update_transcript_context(
            conn,
            KEY,
            "44",
            far.captured_at,
            embedding=[0.0, 0.0, 1.0],
            embedding_model_version="v1",
            lap_number=None,
            position=None,
            track_status=None,
            track_status_flags=None,
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT audio_path FROM radio_transcripts ORDER BY embedding <=> '[1,0,0]' LIMIT 1"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "close.mp3"
