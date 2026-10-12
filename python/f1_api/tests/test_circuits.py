"""Circuit pages (ROADMAP S2): every circuit, and one circuit's measured
figures with the races held there and how each was run. Runs on the
checked-in kernel fixture (2024 Bahrain and Saudi Arabia), no network."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from f1_api.circuits import _strategy  # pyright: ignore[reportPrivateUsage]
from f1_api.config import Settings
from f1_api.main import create_app
from f1_kernel import list_circuits

FIXTURE = Path(__file__).resolve().parents[2] / "f1_kernel/tests/fixtures/historical"


def _client() -> TestClient:
    return TestClient(create_app(Settings(historical_root=FIXTURE)))


def test_lists_every_shipped_circuit_with_its_measured_pit_loss() -> None:
    circuits = _client().get("/circuits").json()
    assert [c["circuit_id"] for c in circuits] == list_circuits()
    marina = next(c for c in circuits if c["circuit_id"] == "marina_bay")
    assert marina["pit_loss_provenance"] == "calibrated"
    assert marina["pit_loss_n_stops"] and marina["pit_loss_uncertainty_s"]
    assert marina["outline"]["points"]


def test_a_circuit_page_has_its_wear_and_the_races_held_there() -> None:
    page = _client().get("/circuits/bahrain").json()
    assert page["name"] == "Bahrain International Circuit"
    assert {w["compound"] for w in page["tyre_wear"]} <= {"soft", "medium", "hard"}
    assert all(w["good_fits"] > 0 for w in page["tyre_wear"])
    (race,) = page["races"]
    assert race["key"] == "2024-1-race"
    assert race["strategy"] is None or race["strategy"]["finishers"] > 0
    # Strategy only: nothing on the page names a result.
    assert "winner" not in str(page).lower()


def test_an_unknown_circuit_is_404() -> None:
    assert _client().get("/circuits/nowhere").status_code == 404


def test_a_retirement_is_not_a_strategy() -> None:
    """A car out on lap 2 ran one stint: counted, it would read as a
    no-stop plan (2024 Austin's Hamilton)."""
    results = pd.DataFrame(
        {
            "Abbreviation": ["VER", "NOR", "HAM", "SAR"],
            "Status": ["Finished", "+1 Lap", "Retired", "Lapped"],
        }
    )
    stints = pd.DataFrame(
        {
            "Driver": ["VER", "VER", "NOR", "NOR", "NOR", "HAM", "SAR", "SAR"],
            "Stint": [1, 2, 1, 2, 3, 1, 1, 2],
            "Compound": ["MEDIUM", "HARD", "MEDIUM", "HARD", "SOFT", "MEDIUM", "HARD", "MEDIUM"],
        }
    )
    strategy = _strategy(results, stints)
    assert strategy is not None
    assert strategy.finishers == 3
    assert strategy.stops == {1: 2, 2: 1}
    assert dict(strategy.plans) == {
        ("medium", "hard"): 1,
        ("medium", "hard", "soft"): 1,
        ("hard", "medium"): 1,
    }
