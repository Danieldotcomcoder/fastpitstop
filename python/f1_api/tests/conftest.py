"""Shared fixtures for `f1_api`'s tests. Everything runs against the
checked-in historical fixture `api_fixture.HISTORICAL_ROOT` points at, so
the whole suite needs no `data/`, no database and no network — the same
three-layer testing discipline `f1_kernel` (whose fixture this reuses)
documents in docs/PHASE2.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from api_fixture import HISTORICAL_ROOT  # noqa: E402

from f1_api.config import Settings  # noqa: E402
from f1_api.main import create_app  # noqa: E402
from f1_api.routes import sessions as session_routes  # noqa: E402


@pytest.fixture(autouse=True)
def empty_response_caches() -> None:
    """Every test starts with nothing cached, so one test's answers (or a
    test that counts computations) never depend on another's."""
    session_routes.laps_cache.clear()
    session_routes.circuit_cache.clear()


@pytest.fixture
def settings() -> Settings:
    assert HISTORICAL_ROOT.is_dir(), (
        f"expected the f1_kernel checked-in fixture at {HISTORICAL_ROOT} — "
        "has python/f1_kernel/tests/fixtures/historical moved?"
    )
    return Settings(historical_root=HISTORICAL_ROOT)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
