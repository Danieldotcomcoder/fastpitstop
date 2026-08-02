"""Constants and ground-truth access for the checked-in fixture sessions.

A uniquely-named module rather than `conftest` so sibling test modules can
import it directly under pytest's importlib mode (the same pattern
`f1_store/tests/store_data.py` uses).

The fixture itself is two synthetic historical races built by
`python/f1_kernel/fixtures/generate.py`; `truth.json` is the generator's own
exact record of what it built, so tests assert against reality rather than
against the kernel's own output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from f1_domain import SessionKey, SessionType

FIXTURE_DIR = Path(__file__).parent / "fixtures"
HISTORICAL_ROOT = FIXTURE_DIR / "historical"
TRUTH_PATH = FIXTURE_DIR / "truth.json"

SC_RACE = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
"""The fixture race WITH a safety car, a planted anomaly, a dirty-air stint
and one in-lap whose lap time the feed dropped."""

GREEN_RACE = SessionKey(year=2024, round=2, session_type=SessionType.RACE)
"""The fixture race with no safety car — a sector yellow instead."""

ALL_RACES = (SC_RACE, GREEN_RACE)


def truth_key(session_key: SessionKey) -> str:
    """The catalog key a session's ground truth is filed under."""
    return f"{session_key.year}_{session_key.round:02d}_Race"


def load_truth() -> dict[str, Any]:
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def number(value: object) -> float:
    """A polars aggregate as a float.

    `Series.min()/max()/median()` are typed `PythonLiteral | None` because an
    empty series has no minimum, so comparing one to a number is a type
    error. Asserting here keeps the assertions in tests readable and fails
    loudly if an aggregate really is null.
    """
    assert isinstance(value, int | float), f"expected a number, got {value!r}"
    return float(value)
