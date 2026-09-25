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


def main(keys: list[str]) -> None:
    settings = get_settings()
    entries = {session_key_path(e.session_key): e for e in list_sessions(settings)}
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
            print(
                f"{key_path:26s} {(circuit.circuit_id if circuit else '?'):14s} "
                f"{'own ' if geometry.from_session else 'file'} n={len(m):6d} "
                f"p50={np.percentile(m, 50):5.1f}m p90={np.percentile(m, 90):5.1f}m "
                f"p99={np.percentile(m, 99):6.1f}m >15m={100 * np.mean(m > 15):4.1f}% "
                f"dropped={dropped:.2f}%"
            )


def _raw_count(
    settings: Settings, source: EventSource, key: SessionKey, circuit: Circuit | None
) -> int:
    track = _measured_track(settings, source, key, circuit)
    return 0 if track is None else len(track)


if __name__ == "__main__":
    main(sys.argv[1:])
