"""Add each shipped circuit's pit lane, derived from its reference race.

    uv run python python/f1_kernel/tools/build_pit_lanes.py [circuit_id ...]

For every circuit file (or the ones named), loads the same FastF1 session
its outline came from (`geometry_source`), takes every real pit stop's
position samples from pit entry to pit exit, and derives the lane with
`f1_kernel.track_outline.derive_pit_lane`. Writes ONLY the `pit_lane` key:
unlike `build_circuits.py`, which regenerates a whole file (and would reset
a calibrated pit loss to its provisional figure), this leaves every other
field exactly as it is.

Dev-only, like `build_circuits.py`: it imports `fastf1` and needs the FastF1
cache populated for the reference sessions.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

from f1_kernel.circuit import CIRCUITS_DIR
from f1_kernel.track_outline import derive_pit_lane

CACHE_DIR = Path(__file__).parents[3] / "data" / "fastf1_cache"

MAX_TRAVERSE = pd.Timedelta(seconds=120)
"""A pit traverse longer than this is not a stop (a red flag, a retirement
into the garage); its path wanders into the garage and is left out."""

_SOURCE = re.compile(r"(\d{4}) round (\d+) (.+)$")


def traverses(session: Any) -> list[list[tuple[float, float]]]:
    """Each pit stop's (x, y) samples from pit entry to pit exit."""
    laps = pd.DataFrame(session.laps)
    found: list[list[tuple[float, float]]] = []
    for driver, group in laps.groupby("DriverNumber"):
        try:
            positions = pd.DataFrame(session.pos_data[str(driver)])
        except KeyError:
            continue
        ordered = group.sort_values("LapNumber")
        entries = ordered["PitInTime"].tolist()
        exits = ordered["PitOutTime"].tolist()
        for i in range(len(ordered) - 1):
            entered, left = entries[i], exits[i + 1]
            if pd.isna(entered) or pd.isna(left) or not entered < left <= entered + MAX_TRAVERSE:
                continue
            times = positions["SessionTime"]
            xs: list[float] = positions["X"].astype(float).tolist()
            ys: list[float] = positions["Y"].astype(float).tolist()
            inside: list[bool] = ((times >= entered) & (times <= left)).tolist()
            found.append(
                [
                    (x, y)
                    for x, y, keep in zip(xs, ys, inside, strict=True)
                    if keep and (x != 0 or y != 0)
                ]
            )
    return found


def build(fastf1: Any, path: Path) -> str:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    match = _SOURCE.search(document.get("geometry_source", ""))
    if match is None:
        return f"{path.stem}: no reference session in geometry_source; skipped"
    season, round_no, session_name = int(match[1]), int(match[2]), match[3]
    session = fastf1.get_session(season, round_no, session_name)
    session.load(laps=True, telemetry=True, weather=False, messages=False)
    try:
        lane = derive_pit_lane(traverses(session))
    except ValueError as exc:
        return f"{path.stem}: {exc}; skipped"
    document["pit_lane"] = lane.model_dump(mode="json")
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return f"{path.stem}: pit lane {lane.length_m:.0f} m from {lane.source_stops} stops"


def main(argv: list[str]) -> int:
    import fastf1

    warnings.filterwarnings("ignore")
    logging.getLogger("fastf1").setLevel(logging.WARNING)
    fastf1.Cache.enable_cache(str(CACHE_DIR))
    wanted = set(argv)
    for path in sorted(CIRCUITS_DIR.glob("*.json")):
        if wanted and path.stem not in wanted:
            continue
        print(build(fastf1, path), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
