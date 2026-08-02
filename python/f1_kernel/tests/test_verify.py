"""The Phase 2 verification harness runs green on the checked-in fixture and
the checked-in reports — this is what `just verify-phase2` exercises,
guarded here as a normal test too."""

from __future__ import annotations

from pathlib import Path

import pytest

from f1_kernel import verify as verify_module
from f1_kernel.verify import VerificationError, load_truth, verify


def test_verify_passes_with_the_checked_in_fixture_and_reports() -> None:
    lines = verify()
    assert any("PHASE 2 VERIFY: OK" in line for line in lines)
    assert any("CALIBRATED" in line for line in lines)


def test_truth_has_what_verify_needs() -> None:
    truth = load_truth()
    sc_truth = truth["2024_01_Race"]
    assert {"sc_laps_by_driver", "dirty_air_laps", "pit_in_laps", "total_laps"} <= set(sc_truth)
    assert "yellow_laps_by_driver" in truth["2024_02_Race"]


def test_verify_fails_loudly_when_a_report_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed backtest reports back exit criteria 4/5 (directional
    sanity, the undercut milestone) — a missing one must fail loudly, not
    be silently skipped."""
    monkeypatch.setattr(verify_module, "REPORTS_DIR", tmp_path / "no_reports_here")

    with pytest.raises(VerificationError, match=r"degradation_backtest\.md"):
        verify()
