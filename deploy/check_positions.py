"""Check that replays carry measured car positions, without narrating.

Run inside the API container after a deploy, from the deploy path (the
image holds `python/` only, so the script goes in on stdin):

    docker compose -f compose.yml -f compose.prod.yml exec -T api \\
        python - 2025-1-race 2026-14-race < deploy/check_positions.py

For each session it loads the position track exactly as the WebSocket does
(`f1_api.geometry.session_positions`: archive fallback, clock alignment,
off-circuit filtering), then drives the replay
stream on a fake clock and counts the `car_positions` frames it would send.
No narration model is passed, so this spends no LLM quota; opening the page
in a browser would narrate any moment not yet cached.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from f1_api.config import get_settings
from f1_api.geometry import session_positions
from f1_api.sessions import parse_session_key, resolve_session
from f1_api.stream.replay import CAR_POSITIONS_KIND, replay_envelopes
from f1_kernel.circuit import circuit_for_session


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def check(key_path: str) -> bool:
    settings = get_settings()
    key = parse_session_key(key_path)
    with resolve_session(settings, key) as resolved:
        if resolved is None:
            print(f"{key_path}: not found")
            return False
        entry = resolved.entry
        circuit = circuit_for_session(entry.event_name, entry.circuit_name)
        _, track = session_positions(settings, resolved.source, key, circuit)
        if track is None:
            print(f"{key_path}: NO measured positions ({entry.provenance})")
            return False
        clock = _Clock()
        frames = 0
        cars: set[str] = set()
        for envelope in replay_envelopes(
            resolved.source,
            key,
            speed=16,
            positions=track,
            event_name=entry.event_name,
            clock=clock.read,
            sleep=clock.sleep,
            wall_clock=lambda: datetime.now(UTC),
        ):
            if envelope.kind == CAR_POSITIONS_KIND:
                frames += 1
                cars.update(envelope.payload["cars"])
        print(
            f"{key_path}: {len(track)} samples, {frames} frames streamed, "
            f"{len(cars)} cars, {entry.provenance}"
        )
        return frames > 0


if __name__ == "__main__":
    results = [check(arg) for arg in sys.argv[1:]]
    sys.exit(0 if results and all(results) else 1)
