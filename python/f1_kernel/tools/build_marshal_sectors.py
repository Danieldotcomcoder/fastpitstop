"""Add each shipped circuit's marshal sectors: where every stretch a
sector-scoped flag covers ("YELLOW IN TRACK SECTOR 7") starts.

    uv run python python/f1_kernel/tools/build_marshal_sectors.py [circuit_id ...]

Race control numbers yellow flags by marshal sector, and nothing in the feed
or the archive says where those are. MultiViewer's circuit API does
(`api.multiviewer.app/api/v1/circuits/<circuit key>/<season>`, unofficial
and undocumented): a position per marshal sector, in the feed's own
coordinates. The circuit key comes from OpenF1's meetings. Each position is
projected onto OUR outline, after aligning MultiViewer's track to it (a
season's frame can sit a few metres off another's), and stored as a
reference-lap distance like a corner's. A circuit whose track does not fit
ours closely, or whose sectors do not run round the lap in number order, is
reported and left alone: never guessed.

Writes ONLY `marshal_sectors` and `marshal_sectors_source`, leaving every
other field as it is (like `build_pit_lanes.py`). Needs the network.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request
from itertools import pairwise
from pathlib import Path
from typing import Any

from f1_kernel.circuit import CIRCUITS_DIR

OPENF1 = "https://api.openf1.org/v1/meetings?year={year}"
MULTIVIEWER = "https://api.multiviewer.app/api/v1/circuits/{key}/{year}"
SEASONS = (2026, 2025, 2024)
MAX_MEDIAN_FIT_M = 5.0
"""How far MultiViewer's track may sit from our outline, median over its
points after alignment, for its marshal positions to be trusted."""

Point = tuple[float, float]


def _get(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "fastpitstop-build/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def project(outline: list[Point], q: Point) -> tuple[float, float]:
    """(distance from `q` to the outline, fraction of the lap at the nearest
    point), in outline units."""
    best = (math.inf, 0.0)
    along = 0.0
    total = sum(math.dist(a, b) for a, b in pairwise(outline))
    for a, b in pairwise(outline):
        length = math.dist(a, b)
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = (
            0.0
            if length == 0
            else max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / length**2))
        )
        gap = math.dist(q, (a[0] + t * dx, a[1] + t * dy))
        if gap < best[0]:
            best = (gap, (along + t * length) / total)
        along += length
    return best


def aligned(outline: list[Point], track: list[Point]) -> tuple[float, float]:
    """The translation that best lays `track` over `outline` (two rounds of
    nearest-point averaging)."""
    shift = (0.0, 0.0)
    sample = track[:: max(1, len(track) // 150)]
    for _ in range(2):
        dx = dy = 0.0
        for x, y in sample:
            q = (x + shift[0], y + shift[1])
            nearest = min(outline, key=lambda p: math.dist(p, q))
            dx += nearest[0] - q[0]
            dy += nearest[1] - q[1]
        shift = (shift[0] + dx / len(sample), shift[1] + dy / len(sample))
    return shift


def _circuit_keys() -> dict[str, tuple[int, int]]:
    """OpenF1's circuit short name -> (circuit key, latest season raced)."""
    keys: dict[str, tuple[int, int]] = {}
    for season in SEASONS:
        for meeting in _get(OPENF1.format(year=season)):
            name = meeting.get("circuit_short_name")
            if isinstance(name, str) and name not in keys and not meeting.get("is_cancelled"):
                keys[name] = (int(meeting["circuit_key"]), season)
        time.sleep(1.0)
    return keys


def build(path: Path, keys: dict[str, tuple[int, int]]) -> str:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    names = [*document.get("feed_circuit_names", []), *document.get("schedule_locations", [])]
    match = next((keys[name] for name in names if name in keys), None)
    outline_doc: dict[str, Any] = document.get("outline") or {}
    outline_raw: list[list[float]] = outline_doc.get("points") or []
    if match is None or not outline_raw:
        return "skipped: no circuit key" if match is None else "skipped: no outline"
    key, season = match
    try:
        data = _get(MULTIVIEWER.format(key=key, year=season))
    except urllib.error.HTTPError as error:
        return f"skipped: MultiViewer answered {error.code} for {key}/{season}"
    finally:
        time.sleep(1.0)
    sectors = sorted(data.get("marshalSectors") or [], key=lambda s: s["number"])
    if not sectors:
        return f"skipped: MultiViewer has no marshal sectors for {key}/{season}"
    outline = [(float(p[0]), float(p[1])) for p in outline_raw]
    track = list(zip(data["x"], data["y"], strict=True))
    sx, sy = aligned(outline, track)
    fits = sorted(project(outline, (x + sx, y + sy))[0] for x, y in track[::5])
    scale = document["lap_distance_m"] / sum(math.dist(a, b) for a, b in pairwise(outline))
    median_fit_m = fits[len(fits) // 2] * scale
    if median_fit_m > MAX_MEDIAN_FIT_M:
        return f"skipped: MultiViewer's track sits {median_fit_m:.1f} m from ours"
    lap = document["lap_distance_m"]
    starts: list[tuple[int, float]] = []
    for sector in sectors:
        q = (sector["trackPosition"]["x"] + sx, sector["trackPosition"]["y"] + sy)
        starts.append((int(sector["number"]), round(project(outline, q)[1] * lap, 1)))
    # In number order the starts must run once round the lap; the first may
    # sit just before the line, so read the order from sector 1 onward.
    first = starts[0][1]
    offsets = [(start - first) % lap for _, start in starts]
    if offsets != sorted(offsets):
        return "skipped: marshal sectors do not run round the lap in number order"
    document["marshal_sectors"] = [{"number": n, "start_m": start} for n, start in starts]
    document["marshal_sectors_source"] = (
        f"MultiViewer circuit {key} ({season}), projected onto this outline "
        f"(median fit {median_fit_m:.1f} m after a {math.hypot(sx, sy) * scale:.1f} m shift)"
    )
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return f"{len(starts)} sectors from {key}/{season}, fit {median_fit_m:.1f} m"


def main(argv: list[str]) -> int:
    keys = _circuit_keys()
    wanted = set(argv)
    for path in sorted(CIRCUITS_DIR.glob("*.json")):
        if wanted and path.stem not in wanted:
            continue
        print(f"{path.stem:<16} {build(path, keys)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
