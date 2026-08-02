"""One real, network-gated end-to-end pull: skipped by default (and always
in CI), only runs when explicitly opted into.

This is deliberately the only test in the package that touches the real
network - everything else in this package mocks the HTTP layer or injects
a fake FastF1 session (see test_pipeline.py, test_openf1.py). Run it with:

    F1_BACKFILL_RUN_NETWORK_TESTS=1 uv run pytest \
        python/f1_backfill/tests/test_integration_network.py

It pulls 2023 Bahrain GP FP1 (round 1 of the first season OpenF1 covers) -
small, stable, and long since archived, so it should stay cheap and
deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from f1_backfill.fastf1_source import configure_cache
from f1_backfill.pipeline import run_backfill

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_BACKFILL_RUN_NETWORK_TESTS") != "1",
    reason="network-gated: set F1_BACKFILL_RUN_NETWORK_TESTS=1 to run against the real APIs",
)


def test_pulls_2023_bahrain_fp1_end_to_end(tmp_path: Path) -> None:
    configure_cache(tmp_path / "fastf1_cache")

    report = run_backfill(2023, round_filter=1, out_dir=tmp_path / "historical")

    fp1 = next(key for key in report.acquired if key.session_type == "Practice 1")
    session_dir = tmp_path / "historical" / "season=2023" / "round=01" / "session=Practice_1"
    assert (session_dir / "laps.parquet").exists()
    assert (tmp_path / "historical" / "manifest.json").exists()
    assert fp1.round == 1
