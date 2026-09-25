"""`live_session_keys`: which sessions are actually running right now.

DB-gated, same as `test_postgres_sink.py` — this is a SQL query and the
thing worth testing is the SQL. Bring a TimescaleDB up with
`docker compose -f python/f1_store/docker-compose.yml up -d`, then:

    F1_STORE_RUN_DB_TESTS=1 uv run pytest python/f1_store/tests/test_live_sessions.py

The bug it exists for: "present in the store" was treated as "in progress",
so the 2026 Spanish GP read as live for the entire week after it finished.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from f1_domain import DomainEvent, LapCompleted, SessionKey, SessionType
from f1_store import PostgresSink, apply_migrations, live_session_keys

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB",
)

_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)

RUNNING = SessionKey(year=2026, round=15, session_type=SessionType.RACE)
FINISHED = SessionKey(year=2026, round=14, session_type=SessionType.RACE)


@pytest.fixture(scope="module")
def module_database() -> Iterator[str]:
    """One throwaway database for the whole module.

    Module-scoped because applying the migrations is by far the slowest part
    (hypertables, indexes) and nothing here tests the schema — `fresh_db`
    below empties the two tables between cases, which is the isolation these
    tests actually need.
    """
    name = f"pitwall_live_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = f"{_ADMIN_DSN.rsplit('/', 1)[0]}/{name}"
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


@pytest.fixture
def fresh_db(module_database: str) -> Iterator[str]:
    """The module's database, emptied of everything this module writes."""
    with psycopg.connect(module_database) as conn:
        conn.execute("TRUNCATE events, sessions")
        conn.commit()
    yield module_database


def _laps(key: SessionKey, at: datetime) -> list[DomainEvent]:
    return [
        LapCompleted(
            session_key=key,
            ts_utc=at + timedelta(seconds=i),
            source_seq=i,
            driver="1",
            lap_number=i,
        )
        for i in range(1, 4)
    ]


def _write(dsn: str, events: list[DomainEvent]) -> None:
    with psycopg.connect(dsn) as conn:
        with PostgresSink(conn) as sink:
            sink.write_events(events)
        conn.commit()


def _set_status(dsn: str, key: SessionKey, status: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO sessions (session_year, session_round, session_type, status) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (session_year, session_round, session_type) "
            "DO UPDATE SET status = EXCLUDED.status",
            (key.year, key.round, key.session_type.value, status),
        )
        conn.commit()


def test_a_session_still_receiving_events_is_live(fresh_db: str) -> None:
    _write(fresh_db, _laps(RUNNING, datetime.now(UTC) - timedelta(minutes=1)))
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == {RUNNING}


def test_a_session_whose_log_stopped_growing_is_not_live(fresh_db: str) -> None:
    """The regression, in its purest form: last week's race is still in the
    log and must not read as running."""
    _write(fresh_db, _laps(FINISHED, datetime.now(UTC) - timedelta(days=6)))
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == set()


def test_a_terminal_status_retires_a_session_immediately(fresh_db: str) -> None:
    """After the flag the feed keeps talking for a while, so freshness alone
    would hold a finished race live for the rest of the window. The feed's
    own word for it is the faster signal."""
    _write(fresh_db, _laps(FINISHED, datetime.now(UTC) - timedelta(minutes=1)))
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == {FINISHED}, "fresh, so live until told otherwise"
    _set_status(fresh_db, FINISHED, "Ends")
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == set()


@pytest.mark.parametrize("status", ["Started", "Inactive", "Aborted", "Something New"])
def test_a_non_terminal_status_leaves_a_fresh_session_live(fresh_db: str, status: str) -> None:
    """ "Aborted" is a red flag the session resumes from, "Inactive" precedes
    one, and an unrecognised value must fall through to the freshness test
    rather than be guessed at — the feed's status vocabulary is open."""
    _write(fresh_db, _laps(RUNNING, datetime.now(UTC) - timedelta(minutes=1)))
    _set_status(fresh_db, RUNNING, status)
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == {RUNNING}


def test_the_window_is_configurable(fresh_db: str) -> None:
    _write(fresh_db, _laps(RUNNING, datetime.now(UTC) - timedelta(minutes=30)))
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == set()
        assert live_session_keys(conn, within=timedelta(hours=1)) == {RUNNING}


def test_only_the_running_session_is_returned(fresh_db: str) -> None:
    """A weekend's worth of history alongside the session in progress."""
    _write(fresh_db, _laps(FINISHED, datetime.now(UTC) - timedelta(days=6)))
    _write(fresh_db, _laps(RUNNING, datetime.now(UTC) - timedelta(minutes=2)))
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == {RUNNING}


def test_an_empty_log_has_nothing_live(fresh_db: str) -> None:
    with psycopg.connect(fresh_db) as conn:
        assert live_session_keys(conn) == set()
