"""DB-backed tests for `PostgresSearchIndex`: the same semantic-ranking +
structured-filter claims `test_search.py` proves against
`MemorySearchIndex`, run here against a real pgvector-backed
`radio_transcripts` table, so the SQL itself (not just the in-memory
fallback) is exercised.

Gated (and skipped in CI): needs a real TimescaleDB WITH pgvector -- see
`test_migrations.py`'s own docstring for how to bring one up.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from radio_data import KEY

from f1_radio.search import PostgresSearchIndex, SearchFilters
from f1_radio.store import TranscriptRecord, insert_transcript, update_transcript_context
from f1_store import apply_migrations

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB+pgvector",
)

_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)

_T0 = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
def fresh_db() -> Iterator[str]:
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


def _seed(
    conn: psycopg.Connection,
    *,
    audio_path: str,
    driver: str = "44",
    captured_at: datetime = _T0,
    text: str = "front tyres are completely gone, box box",
    embedding: list[float] | None = None,
    lap_number: int | None = None,
    track_status: str | None = None,
) -> None:
    record = TranscriptRecord(
        session_key=KEY,
        driver=driver,
        captured_at=captured_at,
        audio_path=audio_path,
        transcript_text=text,
        language="en",
        confidence=0.9,
        model_version="faster-whisper-small",
        segments=None,
    )
    insert_transcript(conn, record)
    if embedding is not None:
        update_transcript_context(
            conn,
            KEY,
            driver,
            captured_at,
            embedding=embedding,
            embedding_model_version="fake-model-v1",
            lap_number=lap_number,
            position=None,
            track_status=track_status,
            track_status_flags=[],
        )


def test_semantic_search_ranks_the_closer_vector_first(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        _seed(conn, audio_path="close.mp3", embedding=[1.0, 0.0, 0.0])
        _seed(
            conn,
            audio_path="far.mp3",
            captured_at=_T0 + timedelta(minutes=1),
            embedding=[0.0, 0.0, 1.0],
        )

        results = PostgresSearchIndex(conn).search(
            [1.0, 0.0, 0.0], filters=SearchFilters(), limit=10
        )

    assert next(r.record.audio_path for r in results) == "close.mp3"
    assert results[0].similarity > results[1].similarity


def test_driver_and_lap_filters_narrow_results(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        _seed(conn, audio_path="a.mp3", driver="44", lap_number=20, embedding=[1.0, 0.0, 0.0])
        _seed(
            conn,
            audio_path="b.mp3",
            driver="1",
            lap_number=20,
            captured_at=_T0 + timedelta(minutes=1),
            embedding=[1.0, 0.0, 0.0],
        )
        _seed(
            conn,
            audio_path="c.mp3",
            driver="44",
            lap_number=5,
            captured_at=_T0 + timedelta(minutes=2),
            embedding=[1.0, 0.0, 0.0],
        )

        results = PostgresSearchIndex(conn).search(
            [1.0, 0.0, 0.0], filters=SearchFilters(driver="44", lap_number=20), limit=10
        )

    assert [r.record.audio_path for r in results] == ["a.mp3"]


def test_no_query_embedding_returns_newest_first_and_null_similarity(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        _seed(conn, audio_path="older.mp3")
        _seed(conn, audio_path="newer.mp3", captured_at=_T0 + timedelta(minutes=5))

        results = PostgresSearchIndex(conn).search(None, filters=SearchFilters(), limit=10)

    assert [r.record.audio_path for r in results] == ["newer.mp3", "older.mp3"]
    assert all(r.similarity == 0.0 for r in results)


def test_a_record_with_no_embedding_is_excluded_from_a_semantic_query(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        _seed(conn, audio_path="no_embedding.mp3")  # never indexed

        results = PostgresSearchIndex(conn).search(
            [1.0, 0.0, 0.0], filters=SearchFilters(), limit=10
        )

    assert results == []
