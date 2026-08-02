"""f1_store test fixtures. The data itself lives in `store_data` (a
uniquely-named module) so sibling test modules can import the constants
without a cross-package `conftest` name clash under importlib mode."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Put the tests dir on sys.path so `import store_data` resolves.
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from store_data import build_sample_events, build_sample_state  # noqa: E402

from f1_domain import DomainEvent, SessionState  # noqa: E402


@pytest.fixture
def sample_events() -> list[DomainEvent]:
    return build_sample_events()


@pytest.fixture
def sample_state() -> SessionState:
    return build_sample_state()
