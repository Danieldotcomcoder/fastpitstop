"""Phase 4.5's DB-free radio fixture mode (`f1_api.radio_fixture`,
`F1_API_RADIO_FIXTURE`) — the seam `just verify-phase4`'s Playwright
harness relies on to get real search/timeline data out of a REAL running
`f1_api` process with no Postgres. `test_radio.py` already covers the REST
surface's own logic against `app.dependency_overrides`; this file instead
proves the env-var-driven wiring those overrides stand in for during a
pytest run — that `Settings.radio_fixture_path` alone (no override) is
enough to make every radio dependency resolve to the checked-in fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from f1_api.config import RADIO_FIXTURE_ENV_VAR, Settings
from f1_api.main import create_app
from f1_api.radio_fixture import load_radio_fixture

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "f1_radio" / "tests" / "fixtures"
HISTORICAL_ROOT = FIXTURE_ROOT / "e2e_historical"
RADIO_FIXTURE_PATH = FIXTURE_ROOT / "e2e_radio_fixture.json"
SESSION_KEY = "2024-1-race"
PARAPHRASE_QUERY = "there's nothing left up front, box this lap"


@pytest.fixture
def fixture_settings() -> Settings:
    assert RADIO_FIXTURE_PATH.is_file(), (
        f"expected the checked-in Phase 4.5 fixture at {RADIO_FIXTURE_PATH} — "
        "regenerate with `uv run python python/f1_radio/fixtures/generate_e2e.py`"
    )
    return Settings(historical_root=HISTORICAL_ROOT, radio_fixture_path=RADIO_FIXTURE_PATH)


def test_load_radio_fixture_parses_every_clip() -> None:
    fixture = load_radio_fixture(RADIO_FIXTURE_PATH)

    assert len(fixture.records) == 4
    assert {r.driver for r in fixture.records} == {"44", "1"}
    assert PARAPHRASE_QUERY in fixture.query_embeddings


def test_settings_reads_radio_fixture_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RADIO_FIXTURE_ENV_VAR, str(RADIO_FIXTURE_PATH))
    from f1_api.config import get_settings

    settings = get_settings()

    assert settings.radio_fixture_path == RADIO_FIXTURE_PATH


def test_search_over_the_fixture_ranks_the_paraphrase_first(fixture_settings: Settings) -> None:
    client = TestClient(create_app(fixture_settings))

    response = client.get("/radio/search", params={"q": PARAPHRASE_QUERY})

    assert response.status_code == 200
    results = response.json()
    assert results[0]["clip"]["driver"] == "44"
    assert results[0]["clip"]["transcript_text"] == "Box box, box this lap for the hard tyre"
    assert results[0]["similarity"] > results[1]["similarity"]


def test_search_blank_query_still_returns_every_fixture_clip(fixture_settings: Settings) -> None:
    client = TestClient(create_app(fixture_settings))

    response = client.get("/radio/search", params={"q": ""})

    assert response.status_code == 200
    assert len(response.json()) == 4


def test_session_radio_over_the_fixture_joins_transcript_and_audio_url(
    fixture_settings: Settings,
) -> None:
    client = TestClient(create_app(fixture_settings))

    response = client.get(f"/sessions/{SESSION_KEY}/radio")

    assert response.status_code == 200
    clips = response.json()
    assert len(clips) == 4
    assert clips[0]["driver"] == "44"
    assert clips[0]["has_transcript"] is True
    assert clips[0]["audio_url"] == "https://example.invalid/radio/44-1.mp3"
    # The planted low-confidence (safety-car) clip stays labeled, never
    # silently rounded up — CLAUDE.md's Phase 4 honesty contract.
    low_confidence = next(c for c in clips if c["track_status"] == "sc")
    assert low_confidence["confidence"] == pytest.approx(0.35)


def test_stream_replay_over_the_fixture_enriches_radio_clip_envelopes(
    fixture_settings: Settings,
) -> None:
    client = TestClient(create_app(fixture_settings))

    with client.websocket_connect(f"/stream/{SESSION_KEY}?mode=replay&speed=0") as websocket:
        envelopes = []
        while True:
            try:
                envelopes.append(websocket.receive_json())
            except Exception:
                break

    radio_envelopes = [e for e in envelopes if e["kind"] == "radio_clip"]
    assert len(radio_envelopes) == 4
    assert [e["payload"]["driver"] for e in radio_envelopes] == ["44", "1", "44", "1"]
    assert all(e["payload"]["has_transcript"] for e in radio_envelopes)
    # event_time_utc must never regress — the same spoiler-safety pacing
    # invariant test_stream_replay.py asserts for every other envelope kind.
    times = [e["event_time_utc"] for e in envelopes]
    assert times == sorted(times)
