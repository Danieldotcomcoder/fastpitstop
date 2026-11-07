"""Add each shipped circuit's pace profile: how far round the lap a car is at
each moment of a clean lap.

    uv run python python/f1_kernel/tools/build_pace_profiles.py [circuit_id ...]

What it is for: the live feed says when each car completes each of the
timing screen's mini-sectors, never where they are, and it withholds car
positions from our client. A pace profile turns the one into the other: a
mini-sector that cars complete, on average, 31% of the way through their
lap time ends where the profile puts a car at 31%. The same profile
places an estimated car between timing points, slowing for corners.
Checked on the 2026 Dutch GP: mini-sector ends placed this way land a
median 7 m (at most 25 m) from MultiViewer's own drawing of them.

How it is measured: OpenF1's free positions (about 4 a second) for the
winner of the latest race at the circuit, projected onto OUR outline after
aligning OpenF1's frame to it. Laps are cut where the car crosses the
outline's start (so no second clock is involved), kept when within
`CLEAN_LAP_RATIO` of the median lap (pit laps, safety cars and first laps
fall out), and the profile is the median, across them, of the lap fraction
covered at each 1% of lap time. A race with no data, or whose positions do
not fit our outline (a season's frame can sit well off another's), gives
way to the next most recent; a circuit with none usable is reported and
left alone.

With no circuit named, builds every circuit that has no profile yet; a
circuit named is rebuilt. Writes ONLY `pace_profile`, leaving every other
field as it is. Needs the network; OpenF1's free tier allows 30 requests a
minute, which `_get` keeps to.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from build_marshal_sectors import SEASONS, aligned
from numpy.typing import NDArray

from f1_kernel.circuit import CIRCUITS_DIR

OPENF1 = "https://api.openf1.org/v1/"
STEPS = 100
"""The profile's resolution: one value per 1% of lap time."""
CLEAN_LAP_RATIO = 1.03
MIN_CLEAN_LAPS = 8
MAX_MEDIAN_FIT_M = 8.0
"""How far OpenF1's positions may sit from our outline, median, after
alignment."""
MAX_CANDIDATES = 3
"""Races tried per circuit, newest first."""


def _get(path: str) -> Any:
    request = urllib.request.Request(OPENF1 + path, headers={"User-Agent": "fastpitstop-build/1.0"})
    for _ in range(4):
        time.sleep(2.1)  # OpenF1's free tier: 30 requests a minute
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(15)
        except TimeoutError:
            time.sleep(15)
    raise RuntimeError(f"OpenF1 kept failing {path}")


def _finished_races() -> list[dict[str, Any]]:
    """Every finished race OpenF1 has, newest first."""
    now = datetime.now(UTC)
    races: list[dict[str, Any]] = []
    for season in SEASONS:
        races += [
            session
            for session in _get(f"sessions?year={season}&session_name=Race")
            if datetime.fromisoformat(session["date_end"]) < now
        ]
    return sorted(races, key=lambda session: session["date_start"], reverse=True)


def _projector(outline: NDArray[np.float64]) -> Any:
    starts, ends = outline[:-1], outline[1:]
    seg = ends - starts
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])[:-1]
    total = float(lengths.sum())

    def project(points: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """(distance to the outline, lap fraction) for each point."""
        rel = points[:, None, :] - starts[None, :, :]
        t = np.clip((rel * seg[None, :, :]).sum(axis=2) / np.maximum(lengths**2, 1e-9), 0.0, 1.0)
        nearest = starts[None, :, :] + t[:, :, None] * seg[None, :, :]
        gaps = np.hypot(*(points[:, None, :] - nearest).transpose(2, 0, 1))
        best = gaps.argmin(axis=1)
        rows = np.arange(len(points))
        along = cumulative[best] + t[rows, best] * lengths[best]
        return gaps[rows, best], along / total

    return project, total


def build(path: Path, races: list[dict[str, Any]], *, force: bool) -> str:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if document.get("pace_profile") and not force:
        return "kept: already has a pace profile"
    names = [*document.get("feed_circuit_names", []), *document.get("schedule_locations", [])]
    outline_doc: dict[str, Any] = document.get("outline") or {}
    outline_raw: list[list[float]] = outline_doc.get("points") or []
    if not outline_raw:
        return "skipped: no outline"
    candidates = [race for race in races if race.get("circuit_short_name") in names]
    if not candidates:
        return "skipped: no race here in OpenF1"
    reasons: list[str] = []
    for race in candidates[:MAX_CANDIDATES]:
        try:
            outcome = _measure(document, outline_raw, race)
        except urllib.error.HTTPError as error:
            outcome = f"OpenF1 answered {error.code}"
        if isinstance(outcome, dict):
            document["pace_profile"] = outcome
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            return str(outcome["source"])
        reasons.append(f"{race['year']}: {outcome}")
    return "skipped: " + "; ".join(reasons)


def _measure(
    document: dict[str, Any], outline_raw: list[list[float]], race: dict[str, Any]
) -> dict[str, Any] | str:
    """The pace profile one race gives, or why it gives none."""
    key = race["session_key"]
    result: list[dict[str, Any]] = _get(f"session_result?session_key={key}&position=1")
    if not result:
        return f"no result for session {key}"
    driver = result[0]["driver_number"]
    rows: list[dict[str, Any]] = _get(f"location?session_key={key}&driver_number={driver}")
    rows = [r for r in rows if r.get("x") is not None and (r["x"], r["y"]) != (0, 0)]
    if len(rows) < 1000:
        return f"only {len(rows)} positions for car {driver} in session {key}"

    outline = np.array(outline_raw, dtype=np.float64)
    points = np.array([(r["x"], r["y"]) for r in rows], dtype=np.float64)
    seconds = np.array([datetime.fromisoformat(r["date"]).timestamp() for r in rows])
    order = np.argsort(seconds)
    points, seconds = points[order], seconds[order]
    sx, sy = aligned(
        [(float(x), float(y)) for x, y in outline_raw],
        [(float(x), float(y)) for x, y in points[::20].tolist()],
    )
    points = points + np.array([sx, sy])
    project, total = _projector(outline)
    scale = document["lap_distance_m"] / total
    gaps, fractions = project(points)
    median_fit_m = float(np.median(gaps)) * scale
    if median_fit_m > MAX_MEDIAN_FIT_M:
        return f"positions sit {median_fit_m:.1f} m from our outline"

    # A lap starts where the fraction wraps from near 1 to near 0.
    wraps: list[int] = (np.where((fractions[:-1] > 0.9) & (fractions[1:] < 0.1))[0] + 1).tolist()
    laps: list[tuple[float, float, NDArray[np.float64], NDArray[np.float64]]] = []
    for a, b in pairwise(wraps):
        start, end = float(seconds[a]), float(seconds[b])
        laps.append((start, end - start, seconds[a:b], fractions[a:b]))
    durations = [d for _, d, _, _ in laps if d > 30]
    if not durations:
        return "no laps found"
    typical = statistics.median(durations)
    low, high = typical / CLEAN_LAP_RATIO, typical * CLEAN_LAP_RATIO
    clean = [lap for lap in laps if low <= lap[1] <= high]
    if len(clean) < MIN_CLEAN_LAPS:
        return f"{len(clean)} clean laps"
    taus = np.linspace(0.0, 1.0, STEPS + 1)
    curves: list[NDArray[np.float64]] = []
    for start, duration, ts, fs in clean:
        tau = (ts - start) / duration
        curve = np.interp(taus, np.concatenate([tau, [1.0]]), np.concatenate([fs, [1.0]]))
        curves.append(np.maximum.accumulate(curve))
    stacked = np.array(curves)
    profile: NDArray[np.float64] = np.maximum.accumulate(np.median(stacked, axis=0))
    profile[0], profile[-1] = 0.0, 1.0
    upper, lower = np.percentile(stacked, [75, 25], axis=0)
    spread_m = float(np.median(upper - lower)) * document["lap_distance_m"]
    source = (
        f"OpenF1 positions, {race['year']} {race['circuit_short_name']} race "
        f"(session {key}), car {driver}: {len(clean)} clean laps of {typical:.1f} s; "
        f"median fit {median_fit_m:.1f} m, median spread {spread_m:.1f} m"
    )
    fractions_out: list[float] = [round(float(v), 5) for v in profile.tolist()]
    return {"distance_fractions": fractions_out, "source": source}


def main(argv: list[str]) -> int:
    wanted = set(argv)
    races = _finished_races()
    for path in sorted(CIRCUITS_DIR.glob("*.json")):
        if wanted and path.stem not in wanted:
            continue
        try:
            outcome = build(path, races, force=bool(wanted))
        except (OSError, RuntimeError, ValueError) as error:
            outcome = f"skipped: {error}"
        print(f"{path.stem:<16} {outcome}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
