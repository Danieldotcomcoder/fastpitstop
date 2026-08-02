"""Regenerate tests/fixtures/*.json from a REAL recording.

Usage:

    uv run python python/f1_parse/tests/make_fixtures.py data/recordings/<dir> [N]

Samples up to N (default 3) messages per known topic out of the
recording's stream and writes them into tests/fixtures/, one JSON file
per topic (a list of raw payloads, verbatim). The current fixtures are
hand-written from the known feed shapes because no real recording exists
yet (see docs/PHASE0.md); run this after the first recorded weekend so
the decoder tests pin against reality instead.

`.z` topics are deliberately NOT sampled to files: their payloads are
opaque base64 strings that would pin nothing readable. The compressed-
topic tests build payloads with `conftest.encode_z` instead, exercising
the real inflation path with known content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import JsonValue

from f1_recording import RecordingReader

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TOPIC_TO_FILENAME = {
    "TimingData": "timing_data.json",
    "WeatherData": "weather_data.json",
    "TrackStatus": "track_status.json",
    "RaceControlMessages": "race_control_messages.json",
    "TeamRadio": "team_radio.json",
    "SessionInfo": "session_info.json",
    "DriverList": "driver_list.json",
}


def sample_fixtures(recording_dir: Path, per_topic: int = 3) -> dict[str, int]:
    """Sample payloads per topic; returns {filename: sampled count}."""
    sampled: dict[str, list[JsonValue]] = {name: [] for name in TOPIC_TO_FILENAME.values()}
    for record in RecordingReader(recording_dir).records():
        filename = TOPIC_TO_FILENAME.get(record.topic)
        if filename is None or len(sampled[filename]) >= per_topic:
            continue
        sampled[filename].append(record.payload)

    counts: dict[str, int] = {}
    for filename, payloads in sampled.items():
        if not payloads:
            continue  # keep the existing fixture rather than emptying it
        (FIXTURES_DIR / filename).write_text(
            json.dumps(payloads, indent=2) + "\n", encoding="utf-8"
        )
        counts[filename] = len(payloads)
    return counts


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    recording_dir = Path(argv[0])
    per_topic = int(argv[1]) if len(argv) > 1 else 3
    counts = sample_fixtures(recording_dir, per_topic)
    for filename, count in sorted(counts.items()):
        print(f"{filename}: {count} payload(s)")
    if not counts:
        print("No known topics found in that recording.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
