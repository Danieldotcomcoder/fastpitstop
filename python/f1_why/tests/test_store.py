"""`f1_why.store`: explanations that survive a restart.

The serialization round trip and the degrade-don't-raise behaviour run
everywhere. The real persistence test is DB-gated like `f1_store`'s own:

    F1_STORE_RUN_DB_TESTS=1 F1_STORE_TEST_DSN=postgresql://... \\
        uv run pytest python/f1_why/tests/test_store.py
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from f1_store import apply_migrations
from f1_why import NARRATION_VERSION, PostgresExplanationStore
from f1_why.narration import Explanation, ExplanationConfidence
from f1_why.store import explanation_from_json, explanation_to_json

_GROUNDED = Explanation(
    anomaly_id="pace_loss:31:16:1780000000",
    text="Leclerc lost 0.96 s after locking the front-right into Turn 10.",
    cited_evidence_ids=("anomaly", "race_control:2026-09-13T13:29:21+00:00:13601"),
    confidence=ExplanationConfidence.HIGH,
    grounded=True,
    fallback_reason=None,
)
_DECLINED = Explanation(
    anomaly_id="pace_loss:27:10:1780000000",
    text="No clear cause found in the available evidence.",
    cited_evidence_ids=(),
    confidence=ExplanationConfidence.LOW,
    grounded=False,
    fallback_reason="model_declined",
)


@pytest.mark.parametrize("explanation", [_GROUNDED, _DECLINED], ids=["grounded", "declined"])
def test_an_explanation_round_trips_through_json(explanation: Explanation) -> None:
    assert explanation_from_json(explanation_to_json(explanation)) == explanation


def test_an_unreachable_database_degrades_to_memory_instead_of_raising() -> None:
    """The store is an optimisation, never a dependency: with the database
    gone, a race keeps streaming and the explanation is still served from
    memory within this process."""
    store = PostgresExplanationStore("postgresql://nobody:nothing@127.0.0.1:1/none")

    assert store.get(_GROUNDED.anomaly_id) is None
    store.set(_GROUNDED.anomaly_id, _GROUNDED)
    assert store.get(_GROUNDED.anomaly_id) == _GROUNDED


# --- DB-gated -----------------------------------------------------------------

_db = pytest.mark.skipif(
    os.environ.get("F1_STORE_RUN_DB_TESTS") != "1",
    reason="DB-gated: set F1_STORE_RUN_DB_TESTS=1 (and F1_STORE_TEST_DSN) for TimescaleDB",
)
_ADMIN_DSN = os.environ.get(
    "F1_STORE_TEST_DSN", "postgresql://pitwall:pitwall@localhost:5432/pitwall"
)


@pytest.fixture
def migrated_dsn() -> Iterator[str]:
    name = f"pitwall_expl_{uuid.uuid4().hex[:12]}"
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


@_db
def test_an_explanation_outlives_the_process_that_wrote_it(migrated_dsn: str) -> None:
    """The point of the table: a fresh store (a restarted API, a new deploy)
    reads what an earlier one paid a model for."""
    PostgresExplanationStore(migrated_dsn).set(_GROUNDED.anomaly_id, _GROUNDED)

    assert PostgresExplanationStore(migrated_dsn).get(_GROUNDED.anomaly_id) == _GROUNDED


@_db
def test_a_new_narration_version_does_not_serve_old_sentences(migrated_dsn: str) -> None:
    """Changing the prompt or the evidence rules must re-narrate, not serve
    text written under the old rules."""
    PostgresExplanationStore(migrated_dsn, version="old").set(_GROUNDED.anomaly_id, _GROUNDED)

    assert (
        PostgresExplanationStore(migrated_dsn, version=NARRATION_VERSION).get(_GROUNDED.anomaly_id)
        is None
    )


@_db
def test_writing_the_same_anomaly_twice_keeps_the_first(migrated_dsn: str) -> None:
    """Never regenerate, never overwrite: two workers narrating the same
    moment at once must not replace one sentence with another."""
    PostgresExplanationStore(migrated_dsn).set(_GROUNDED.anomaly_id, _GROUNDED)
    later = Explanation(
        anomaly_id=_GROUNDED.anomaly_id,
        text="A different sentence.",
        cited_evidence_ids=("anomaly",),
        confidence=ExplanationConfidence.LOW,
        grounded=True,
        fallback_reason=None,
    )
    PostgresExplanationStore(migrated_dsn).set(_GROUNDED.anomaly_id, later)

    assert PostgresExplanationStore(migrated_dsn).get(_GROUNDED.anomaly_id) == _GROUNDED
