"""f1_historical_adapter test fixtures. The synthetic tables live in
`adapter_data` (a uniquely-named module) so sibling test modules can import
the builders without a cross-package `conftest` name clash under importlib
mode."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Put the tests dir on sys.path so `import adapter_data` resolves.
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from adapter_data import build_synthetic_tables  # noqa: E402

from f1_historical_adapter.reader import SessionTables  # noqa: E402


@pytest.fixture
def synthetic_tables() -> SessionTables:
    return build_synthetic_tables()
