"""The Phase 1 verification harness runs green on the checked-in fixture —
this is what `just verify-phase1` exercises, guarded here as a normal test
too."""

from __future__ import annotations

from typing import cast

from f1_pipeline import verify as verify_module
from f1_pipeline.verify import verify


def test_verify_passes_on_the_checked_in_fixture() -> None:
    lines = verify()
    assert any("PHASE 1 VERIFY: OK" in line for line in lines)
    assert any("determinism OK, idempotency OK" in line for line in lines)


def test_expected_snapshot_covers_classification() -> None:
    expected = verify_module.load_expected()
    # Ground-truth classification for all 20 cars is what makes the
    # final-positions check meaningful (not a self-comparison).
    positions = cast("dict[str, int]", expected["final_positions"])
    assert len(positions) == 20
    assert set(expected) >= {
        "events_by_kind",
        "row_counts",
        "final_positions",
        "laps_by_driver",
        "pit_laps",
    }
