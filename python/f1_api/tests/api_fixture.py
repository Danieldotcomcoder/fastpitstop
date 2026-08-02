"""Constants shared across `f1_api`'s tests: the checked-in synthetic
historical fixture this package reuses from `f1_kernel`
(`python/f1_kernel/tests/fixtures/historical`, see that package's own
`tests/kernel_fixture.py`) rather than duplicating a second synthetic race
— it is already exactly "a checked-in historical session" with committed
ground truth, and reusing it keeps one fixture race, not two, in the repo.

A uniquely-named module rather than `conftest` so sibling test modules can
import it directly under pytest's importlib mode (the same pattern
`f1_kernel/tests/kernel_fixture.py` and `f1_store/tests/store_data.py`
both already follow).
"""

from __future__ import annotations

from pathlib import Path

HISTORICAL_ROOT = (
    Path(__file__).resolve().parents[2] / "f1_kernel" / "tests" / "fixtures" / "historical"
)

SESSION_KEY = "2024-1-race"
"""Bahrain GP (round 1): the fixture race WITH a safety car, a planted
anomaly, a dirty-air stint, and (the only fixture race with a
`car_telemetry` table) telemetry — so also the one `/telemetry/{driver}`
is exercised against."""

GREEN_SESSION_KEY = "2024-2-race"
"""Saudi Arabian GP (round 2): the fixture's green-flag race, no telemetry."""
