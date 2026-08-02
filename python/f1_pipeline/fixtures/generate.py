"""Regenerate the checked-in Phase 1 verification fixture.

    uv run python python/f1_pipeline/fixtures/generate.py

Builds the synthetic 2024 round-1 race recording (via f1_parse's
deterministic `race_sim`, the same one the state-machine backtest uses) into
`replay_race/`, then replays it through the REAL pipeline into a MemorySink
and writes `expected.json`:

- `events_by_kind` / `row_counts` are pipeline-derived regression snapshots;
- `final_positions` / `laps_by_driver` / `pit_laps` are the recording's
  independent GROUND TRUTH (from `race_sim`), so verification checks the
  pipeline against reality, not just against itself.

`race_sim` builds byte-identical recordings across runs, so this is
reproducible. It lives under f1_parse/tests, so we add that dir to the path.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Output lives INSIDE the package so verify.py finds it via __file__ and the
# wheel bundles it. This generator sits outside the package (it's dev-only
# and imports test code).
OUTPUT_DIR = Path(__file__).parents[1] / "src" / "f1_pipeline" / "fixtures"
RECORDING_DIR = OUTPUT_DIR / "replay_race"
EXPECTED_PATH = OUTPUT_DIR / "expected.json"

_RACE_SIM_DIR = Path(__file__).parents[2] / "f1_parse" / "tests"
sys.path.insert(0, str(_RACE_SIM_DIR))

from race_sim import build_race_recording  # noqa: E402

from f1_domain import SessionKey, SessionType  # noqa: E402
from f1_pipeline.pipeline import run  # noqa: E402
from f1_pipeline.replay_source import replay_records  # noqa: E402
from f1_store import MemorySink  # noqa: E402

SESSION_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)


def main() -> int:
    if RECORDING_DIR.exists():
        shutil.rmtree(RECORDING_DIR)
    truth = build_race_recording(RECORDING_DIR)

    sink = MemorySink()
    metrics = run(replay_records(RECORDING_DIR, speed=0), sink, session_key=SESSION_KEY)

    expected = {
        "events_by_kind": metrics.events_by_kind,
        "row_counts": sink.row_counts(),
        "final_positions": truth.final_positions,
        "laps_by_driver": truth.laps_by_driver,
        "pit_laps": {driver: list(laps) for driver, laps in truth.pit_laps.items()},
    }
    EXPECTED_PATH.write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"Wrote {RECORDING_DIR} and {EXPECTED_PATH}")
    print(f"  {metrics.records_in} records -> {metrics.events_out} events")
    print(f"  row counts: {sink.row_counts()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
