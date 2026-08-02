"""Shared fixtures for the kernel tests.

Everything here is backed by the CHECKED-IN synthetic historical sessions
(`fixtures/`, built by `fixtures/generate.py`) so the suite runs on a
machine with no backfill, no database and no network. `truth.json` holds
the generator's exact ground truth — which laps ran under the safety car,
which lap is the planted anomaly — so assertions are exact rather than
"looks plausible".

Tests against REAL backfilled races live in `test_real_historical.py` and
skip themselves when `data/historical/` is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Put the tests dir on sys.path so `import kernel_fixture` resolves under
# pytest's importlib mode (the f1_store convention).
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from kernel_fixture import HISTORICAL_ROOT, load_truth  # noqa: E402

from f1_kernel import HistoricalSource, KernelParams  # noqa: E402


@pytest.fixture(scope="session")
def truth() -> dict[str, Any]:
    """Ground truth for every fixture session, keyed as the catalog keys it."""
    return load_truth()


@pytest.fixture
def source() -> HistoricalSource:
    return HistoricalSource(HISTORICAL_ROOT)


@pytest.fixture
def params() -> KernelParams:
    return KernelParams()
