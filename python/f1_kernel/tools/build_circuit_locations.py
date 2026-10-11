"""Record each circuit's FastF1 schedule `Location` names in its file, so a
calendar entry (no session yet) can find its circuit
(`f1_kernel.circuit.circuit_for_schedule`).

Sources, both factual:
- the circuit's own reference event (`geometry_source`: "<season> round <n>
  <session>"), which is where its geometry came from. That covers venues
  that only appear in recent seasons (2026's Madring, Sepang);
- every 2023-2025 round whose Grand Prix name maps to exactly this circuit.
  Those seasons have no venue moves under one name, so the name is safe
  there. It is not for 2026: the "Spanish" and "Bahrain" Grands Prix moved.

    uv run python python/f1_kernel/tools/build_circuit_locations.py
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any

import fastf1
import pandas as pd

from f1_kernel.circuit import CIRCUITS_DIR, circuit_for_event, list_circuits, load_circuit

NAME_SEASONS = (2023, 2024, 2025)
_SOURCE = re.compile(r"(\d{4}) round (\d+)")


def _schedule(season: int) -> pd.DataFrame:
    return pd.DataFrame(fastf1.get_event_schedule(season, include_testing=False))


def main() -> int:
    logging.getLogger("fastf1").setLevel(logging.WARNING)
    schedules: dict[int, pd.DataFrame] = {}

    def schedule(season: int) -> pd.DataFrame:
        if season not in schedules:
            schedules[season] = _schedule(season)
        return schedules[season]

    found: dict[str, set[str]] = defaultdict(set)
    for circuit_id in list_circuits():
        circuit = load_circuit(circuit_id)
        match = _SOURCE.search(circuit.geometry_source or "")
        if match is None:
            continue
        season, round_number = int(match.group(1)), int(match.group(2))
        rows = schedule(season)
        row = rows[rows["RoundNumber"] == round_number]
        if not row.empty:
            found[circuit_id].add(str(row.iloc[0]["Location"]))

    for season in NAME_SEASONS:
        for _, row in schedule(season).iterrows():
            circuit = circuit_for_event(str(row["EventName"]))
            if circuit is not None:
                found[circuit.circuit_id].add(str(row["Location"]))

    for circuit_id in list_circuits():
        path = CIRCUITS_DIR / f"{circuit_id}.json"
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        locations = sorted(found.get(circuit_id, set()))
        # Keep the file's key order: the new field sits after the feed names.
        rebuilt: dict[str, Any] = {}
        for key, value in document.items():
            if key == "schedule_locations":
                continue
            rebuilt[key] = value
            if key == "feed_circuit_names":
                rebuilt["schedule_locations"] = locations
        path.write_text(json.dumps(rebuilt, indent=2) + "\n", encoding="utf-8")
        print(f"{circuit_id:14} {locations}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
