"""Regenerate Phase 4.5's E2E fixture (`just verify-phase4`):

    uv run python python/f1_radio/fixtures/generate_e2e.py

Writes a tiny, dedicated historical session (`e2e_historical/`, its OWN
`manifest.json` catalog — deliberately separate from `f1_kernel`'s own
checked-in fixture at `python/f1_kernel/tests/fixtures/historical`, which
`generate.py` there fully wipes and rebuilds from its own `PLANS` on every
run and pins several tests to an exact session count; adding a radio-only
session there would either get silently deleted on the next regen or break
`test_sources.py`'s `len(list_historical_sessions(...)) == 2` assertion)
carrying only a `team_radio.parquet` table — `f1_historical_adapter`'s
`_session_start` falls back to a table's own `date` column when `laps` is
absent, and nothing this fixture needs (search, the `radio_clip` stream
envelope, spoiler-safety pacing) touches lap/stint/position data at all.

`e2e_radio_fixture.json` is the companion `f1_api.radio_fixture` reads: the
SAME clips (so the two can never drift — this script is their one shared
source of truth) plus each clip's own PLANTED embedding and a couple of
hand-picked paraphrase-query embeddings, letting `just verify-phase4` prove
the search PLUMBING (query -> embed -> rank -> API -> render) without
downloading a real sentence-transformers model in CI — see
`f1_api.radio_fixture`'s own module docstring for why a scripted embedding
fake is the right call here specifically.

Four clips, two drivers, ascending `captured_at` — one deliberately
LOW-confidence (a safety-car call, confidence 0.35) so the frontend's
low-confidence marking has something real to render against.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

FIXTURES_ROOT = Path(__file__).parents[1] / "tests" / "fixtures"
HISTORICAL_ROOT = FIXTURES_ROOT / "e2e_historical"
RADIO_FIXTURE_PATH = FIXTURES_ROOT / "e2e_radio_fixture.json"

SESSION_START = datetime(2024, 5, 1, 14, 0, 0, tzinfo=UTC)
SEASON = 2024
ROUND_NO = 1
EVENT_NAME = "Radio Fixture Grand Prix"

CLIPS: tuple[dict[str, Any], ...] = (
    {
        "driver": "44",
        "offset_min": 5,
        "transcript_text": "Box box, box this lap for the hard tyre",
        "language": "en",
        "confidence": 0.91,
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "lap_number": 12,
        "position": 4,
        "track_status": "green",
        "audio_url": "https://example.invalid/radio/44-1.mp3",
    },
    {
        "driver": "1",
        "offset_min": 8,
        "transcript_text": "Gap to the car behind is one point eight, keep pushing",
        "language": "en",
        "confidence": 0.88,
        "embedding": [0.0, 1.0, 0.0, 0.0],
        "lap_number": 13,
        "position": 1,
        "track_status": "green",
        "audio_url": "https://example.invalid/radio/1-1.mp3",
    },
    {
        "driver": "44",
        "offset_min": 15,
        "transcript_text": "Yellow yellow yellow, safety car deployed",
        "language": "en",
        "confidence": 0.35,
        "embedding": [0.0, 0.0, 1.0, 0.0],
        "lap_number": 20,
        "position": 4,
        "track_status": "sc",
        "audio_url": "https://example.invalid/radio/44-2.mp3",
    },
    {
        "driver": "1",
        "offset_min": 20,
        "transcript_text": "Copy, box box confirmed for the medium tyre",
        "language": "en",
        "confidence": 0.83,
        "embedding": [0.0, 0.0, 0.0, 1.0],
        "lap_number": 24,
        "position": 1,
        "track_status": "green",
        "audio_url": "https://example.invalid/radio/1-2.mp3",
    },
)

# Close to CLIPS[0]'s own [1, 0, 0, 0] but with no lexical overlap at all —
# the paraphrase the search E2E assertion queries for.
QUERY_EMBEDDINGS: dict[str, list[float]] = {
    "there's nothing left up front, box this lap": [0.97, 0.03, 0.0, 0.0],
}
DEFAULT_QUERY_EMBEDDING: list[float] = [0.0, 0.0, 0.0, 0.0]


def main() -> int:
    if HISTORICAL_ROOT.exists():
        shutil.rmtree(HISTORICAL_ROOT)
    directory = HISTORICAL_ROOT / f"season={SEASON}" / f"round={ROUND_NO:02d}" / "session=Race"
    directory.mkdir(parents=True, exist_ok=True)

    captured_ats = [SESSION_START + timedelta(minutes=c["offset_min"]) for c in CLIPS]

    team_radio = pd.DataFrame(
        {
            "date": captured_ats,
            "driver_number": [int(c["driver"]) for c in CLIPS],
            "recording_url": [c["audio_url"] for c in CLIPS],
        }
    )
    team_radio.to_parquet(directory / "team_radio.parquet", index=False)

    catalog = {
        "format_version": 1,
        "sessions": {
            f"{SEASON}_{ROUND_NO:02d}_Race": {
                "season": SEASON,
                "round": ROUND_NO,
                "session_type": "Race",
                "event_name": EVENT_NAME,
                "sources": ["openf1"],
                "tables": {"team_radio": len(CLIPS)},
                "acquired_at": "2024-05-01T00:00:00Z",
            }
        },
    }
    (HISTORICAL_ROOT / "manifest.json").write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    radio_fixture = {
        "session_key": {"year": SEASON, "round": ROUND_NO, "session_type": "race"},
        "clips": [
            {
                "driver": clip["driver"],
                "captured_at": captured_at.isoformat(),
                "audio_path": f"fixture://{clip['driver']}/{captured_at.isoformat()}",
                "transcript_text": clip["transcript_text"],
                "language": clip["language"],
                "confidence": clip["confidence"],
                "model_version": "fixture-whisper-v1",
                "embedding": clip["embedding"],
                "embedding_model_version": "fixture-embed-v1",
                "lap_number": clip["lap_number"],
                "position": clip["position"],
                "track_status": clip["track_status"],
                "track_status_flags": ["sc"] if clip["track_status"] == "sc" else [],
            }
            for clip, captured_at in zip(CLIPS, captured_ats, strict=True)
        ],
        "query_embeddings": QUERY_EMBEDDINGS,
        "default_query_embedding": DEFAULT_QUERY_EMBEDDING,
    }
    RADIO_FIXTURE_PATH.write_text(json.dumps(radio_fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {HISTORICAL_ROOT} and {RADIO_FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
