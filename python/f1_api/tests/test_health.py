"""`/healthz` (liveness), `/readyz` (readiness — the one route that actually
touches the database) and `/version` (image build metadata) — the container
deploy's health-check trio. `/health` itself is covered by `test_rest.py`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from f1_api.config import Settings
from f1_api.main import create_app
from f1_store import apply_migrations
from f1_store.config import connect


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_no_db_configured(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_version_defaults(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"git_sha": "dev", "build_time": "unknown"}


def test_version_reads_settings(settings: Settings) -> None:
    versioned = Settings(
        historical_root=settings.historical_root,
        git_sha="abc1234",
        build_time="2026-01-01T00:00:00Z",
    )
    client = TestClient(create_app(versioned))
    response = client.get("/version")
    assert response.json() == {"git_sha": "abc1234", "build_time": "2026-01-01T00:00:00Z"}


def test_readyz_unreachable_db(settings: Settings) -> None:
    """No real DB needed for this one — the port is just closed."""
    bad_settings = Settings(
        historical_root=settings.historical_root,
        store_dsn="postgresql://pitwall:pitwall@localhost:59999/nope",
    )
    client = TestClient(create_app(bad_settings))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]


pytestmark_db = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB",
)

_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)


@pytest.fixture
def fresh_db() -> Iterator[str]:
    name = f"pitwall_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = _ADMIN_DSN.rsplit("/", 1)[0] + f"/{name}"
    try:
        yield dsn
    finally:
        with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytestmark_db
def test_readyz_empty_db(settings: Settings, fresh_db: str) -> None:
    with connect(fresh_db, autocommit=True) as conn:
        apply_migrations(conn)

    ready_settings = Settings(historical_root=settings.historical_root, store_dsn=fresh_db)
    client = TestClient(create_app(ready_settings))
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["last_ingest_at"] is None


@pytestmark_db
def test_readyz_reports_last_ingest(settings: Settings, fresh_db: str) -> None:
    with connect(fresh_db, autocommit=True) as conn:
        apply_migrations(conn)
        conn.execute(
            "INSERT INTO events "
            "(session_year, session_round, session_type, ts_utc, source_seq, event_index, "
            "kind, payload) VALUES (2024, 1, 'race', now(), 1, 1, 'test', '{}'::jsonb)"
        )

    ready_settings = Settings(historical_root=settings.historical_root, store_dsn=fresh_db)
    client = TestClient(create_app(ready_settings))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["last_ingest_at"] is not None


def test_time_reports_the_server_clock(client: TestClient) -> None:
    """What the frontend corrects a wrong device clock against (Delay Sync
    compares every event's server timestamp with the viewer's clock)."""
    import time

    before = int(time.time() * 1000)
    body = client.get("/time").json()
    after = int(time.time() * 1000)
    assert before <= body["server_time_ms"] <= after
