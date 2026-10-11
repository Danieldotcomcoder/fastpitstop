"""How far measured car positions sit from each circuit's drawn outline.

Run inside the API container (the script goes in on stdin), or locally:

    docker compose -f compose.yml -f compose.prod.yml exec -T api \\
        python - 2025-1-race 2025-8-race < deploy/check_outline_fit.py

For every session named (or every session the API lists, when none is
named) it takes the EXACT geometry and positions the map gets
(`f1_api.geometry.session_positions`: the circuit file's lines, or the
session's own when those do not fit it) and measures each sample's
distance to the nearest drawn line. `own` marks a session drawn from its
own geometry. Read-only; no LLM.

With `--gate` (the release gate, `just verify-real`) it exits 1 when a
session's dots sit further from the drawn lines than `MAX_P90_M` /
`MAX_P99_M` / `MAX_FAR_PCT`, or a named session has no positions to check.
"""

from __future__ import annotations

import sys

import numpy as np

from f1_api.config import Settings, get_settings
from f1_api.geometry import (
    _measured_track,  # pyright: ignore[reportPrivateUsage]
    session_positions,
)
from f1_api.sessions import list_sessions, parse_session_key, resolve_session, session_key_path
from f1_domain import SessionKey
from f1_kernel import Circuit, EventSource, distance_to_lines
from f1_kernel.circuit import circuit_for_session

UNITS_PER_METRE = 10.0

MAX_P90_M = 5.0
MAX_P99_M = 15.0
MAX_FAR_PCT = 1.0
"""`--gate` thresholds (the release gate, `just verify-real`): 90% of dots
within 5 m of a drawn line, 99% within 15 m, at most 1% further than 15 m.
Measured 2026-10-11: 2024 Bahrain p90 1.1 m / p99 4.1 m, 2025 Suzuka (drawn
from its own geometry) p90 1.6 m / p99 4.8 m."""


def main(keys: list[str], *, gate: bool = False) -> int:
    settings = get_settings()
    entries = {session_key_path(e.session_key): e for e in list_sessions(settings)}
    failures: list[str] = []
    measured: set[str] = set()
    for key_path in keys or sorted(entries):
        key = parse_session_key(key_path)
        with resolve_session(settings, key) as resolved:
            if resolved is None:
                continue
            circuit = circuit_for_session(resolved.entry.event_name, resolved.entry.circuit_name)
            raw = _raw_count(settings, resolved.source, key, circuit)
            geometry, track = session_positions(settings, resolved.source, key, circuit)
            if track is None or geometry.outline is None:
                continue
            lines = [geometry.outline.points]
            if geometry.pit_lane is not None:
                lines.append(geometry.pit_lane.points)
            sample = track.gather_every(5)
            m = distance_to_lines(sample["x"].to_numpy(), sample["y"].to_numpy(), lines)
            m = m / UNITS_PER_METRE
            dropped = 100 * (1 - len(track) / raw) if raw else 0.0
            p90, p99 = float(np.percentile(m, 90)), float(np.percentile(m, 99))
            far = float(100 * np.mean(m > 15))
            measured.add(key_path)
            print(
                f"{key_path:26s} {(circuit.circuit_id if circuit else '?'):14s} "
                f"{'own ' if geometry.from_session else 'file'} n={len(m):6d} "
                f"p50={np.percentile(m, 50):5.1f}m p90={p90:5.1f}m "
                f"p99={p99:6.1f}m >15m={far:4.1f}% "
                f"dropped={dropped:.2f}%"
            )
            if p90 > MAX_P90_M or p99 > MAX_P99_M or far > MAX_FAR_PCT:
                failures.append(f"{key_path}: p90 {p90:.1f}m, p99 {p99:.1f}m, >15m {far:.1f}%")
    if not gate:
        return 0
    if not entries:
        print("outline check: SKIPPED (no sessions: no backfill and no store)")
        return 0
    failures.extend(f"{key}: no measured positions to check" for key in keys if key not in measured)
    if failures:
        print("outline check: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"outline check: OK over {len(measured)} session(s)")
    return 0


def _raw_count(
    settings: Settings, source: EventSource, key: SessionKey, circuit: Circuit | None
) -> int:
    track = _measured_track(settings, source, key, circuit)
    return 0 if track is None else len(track)


if __name__ == "__main__":
    args = sys.argv[1:]
    gate = "--gate" in args
    raise SystemExit(main([a for a in args if a != "--gate"], gate=gate))
