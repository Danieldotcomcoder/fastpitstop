"""DB-backed tests: schema applies from scratch, batched COPY writes land,
re-ingesting is idempotent, and the log replays back to the exact events.

Gated (and skipped in CI): needs a real TimescaleDB. Bring one up with
`docker compose -f python/f1_store/docker-compose.yml up -d`, then:

    F1_STORE_RUN_DB_TESTS=1 F1_STORE_TEST_DSN=postgresql://pitwall:pitwall@localhost:5432/pitwall \
        uv run pytest python/f1_store/tests/test_postgres_sink.py

Each test runs against a throwaway database created and dropped around it,
so the schema is genuinely applied "from scratch" every time and nothing
leaks between runs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql
from store_data import EXPECTED_ROW_COUNTS, KEY

from f1_domain import DomainEvent, SessionState
from f1_store import PostgresSink, apply_migrations, replay_events

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB",
)

_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """Create a throwaway database, yield its DSN, drop it afterwards."""
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


def _count(conn: psycopg.Connection[Any], table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def test_schema_applies_from_scratch(fresh_db: str) -> None:
    # fresh_db already applied migrations; re-applying must be a no-op.
    with psycopg.connect(fresh_db) as conn:
        assert apply_migrations(conn) == []
        for table in EXPECTED_ROW_COUNTS:
            assert _count(conn, table) == 0


def test_batched_writes_land_with_expected_counts(
    fresh_db: str, sample_events: list[DomainEvent]
) -> None:
    with psycopg.connect(fresh_db) as conn:
        # batch_size below the event count forces multiple COPY flushes.
        with PostgresSink(conn, batch_size=4) as sink:
            sink.write_events(sample_events)
        for table, expected in EXPECTED_ROW_COUNTS.items():
            assert _count(conn, table) == expected, table


def test_reingesting_twice_leaves_counts_unchanged(
    fresh_db: str, sample_events: list[DomainEvent]
) -> None:
    with psycopg.connect(fresh_db) as conn:
        with PostgresSink(conn, batch_size=4) as sink:
            sink.write_events(sample_events)
        first = {t: _count(conn, t) for t in EXPECTED_ROW_COUNTS}
        # A brand-new sink, same events: ON CONFLICT DO NOTHING must swallow
        # every row.
        with PostgresSink(conn, batch_size=4) as sink:
            sink.write_events(sample_events)
        second = {t: _count(conn, t) for t in EXPECTED_ROW_COUNTS}
        assert first == second == {t: EXPECTED_ROW_COUNTS[t] for t in EXPECTED_ROW_COUNTS}


def test_replay_events_reconstructs_the_stream(
    fresh_db: str, sample_events: list[DomainEvent]
) -> None:
    with psycopg.connect(fresh_db) as conn:
        with PostgresSink(conn) as sink:
            sink.write_events(sample_events)
        replayed = list(replay_events(conn, KEY))
    # Byte-identical to what went in, in the same order: the log is a
    # genuine source of truth.
    assert [e.model_dump_json() for e in replayed] == [e.model_dump_json() for e in sample_events]


def test_write_state_upserts_sessions_and_drivers(
    fresh_db: str, sample_state: SessionState
) -> None:
    with psycopg.connect(fresh_db) as conn:
        with PostgresSink(conn) as sink:
            sink.write_state(sample_state)
            sink.write_state(sample_state)  # upsert again: still one row each
        assert _count(conn, "sessions") == 1
        assert _count(conn, "drivers") == 2
        with conn.cursor() as cur:
            cur.execute(
                "SELECT position, code FROM drivers WHERE number = '44' "
                "AND session_year = 2024 AND session_round = 1 AND session_type = 'race'"
            )
            row = cur.fetchone()
            assert row == (1, "HAM")
