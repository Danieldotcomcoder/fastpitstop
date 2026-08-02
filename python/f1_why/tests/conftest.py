"""Shared fixtures for the `f1_why` test suite.

Puts the tests dir on `sys.path` so `import race_fixture` resolves under
pytest's importlib mode — the same convention `f1_kernel/tests/conftest.py`
uses for its own `kernel_fixture` module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from race_fixture import build_session_frames  # noqa: E402

from f1_kernel import (  # noqa: E402
    KernelParams,
    KernelReport,
    SessionFrames,
    analyze_session_from_frames,
)
from f1_why.anomaly import Anomaly  # noqa: E402
from f1_why.detect import detect_anomalies  # noqa: E402
from f1_why.params import AnomalyParams  # noqa: E402


@pytest.fixture(scope="session")
def kernel_params() -> KernelParams:
    return KernelParams()


@pytest.fixture(scope="session")
def race_frames() -> SessionFrames:
    return build_session_frames()


@pytest.fixture(scope="session")
def race_report(race_frames: SessionFrames, kernel_params: KernelParams) -> KernelReport:
    return analyze_session_from_frames(race_frames, kernel_params)


@pytest.fixture(scope="session")
def anomaly_params() -> AnomalyParams:
    return AnomalyParams()


@pytest.fixture(scope="session")
def anomalies(
    race_frames: SessionFrames, race_report: KernelReport, anomaly_params: AnomalyParams
) -> list[Anomaly]:
    """Every anomaly the checked-in fixture plants, shared read-only across
    `test_detect.py` (which asserts the detector itself) and 5.2/5.3's own
    test files (which assemble evidence/narrate over these SAME anomalies
    rather than re-deriving a second set)."""
    return detect_anomalies(race_frames, race_report, anomaly_params)
