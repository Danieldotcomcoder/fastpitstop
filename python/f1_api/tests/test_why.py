"""Phase 5.4: anomalies + grounded explanations through `f1_api`.

REST tests override `get_narration_model` / `get_explanation_store` (and
optionally the radio search index) via `app.dependency_overrides`, the same
lever `test_radio.py` already uses. Timeline pacing / Delay Sync property
tests drive `replay_envelopes` directly with an explicit model/store — the
radio enrichment tests' own pattern — so they never depend on the process-
level singletons or a live API key.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from api_fixture import HISTORICAL_ROOT, SESSION_KEY
from fastapi.testclient import TestClient

from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.stream.replay import EXPLANATION_KIND, replay_envelopes
from f1_api.why import (
    anomaly_id_path,
    get_explanation_store,
    get_narration_model,
    parse_anomaly_id,
)
from f1_api.why_fixture import CitingNarrationModel
from f1_domain import SessionKey, SessionType
from f1_kernel import HistoricalSource
from f1_why import (
    FALLBACK_TEXT,
    MemoryExplanationStore,
    MockNarrationModel,
    ModelNarration,
)

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
FAKE_WALL_START = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def settings() -> Settings:
    return Settings(historical_root=HISTORICAL_ROOT)


def _client(
    settings: Settings,
    *,
    model: object | None = None,
    store: MemoryExplanationStore | None = None,
) -> TestClient:
    app = create_app(settings)
    resolved_model = model if model is not None else CitingNarrationModel()
    resolved_store = store if store is not None else MemoryExplanationStore()
    app.dependency_overrides[get_narration_model] = lambda: resolved_model
    app.dependency_overrides[get_explanation_store] = lambda: resolved_store
    return TestClient(app)


# -- /sessions/{key}/anomalies ------------------------------------------------


def test_session_anomalies_returns_ranked_salience_for_checked_in_session(
    settings: Settings,
) -> None:
    response = _client(settings).get(f"/sessions/{SESSION_KEY}/anomalies")

    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    assert body == sorted(body, key=lambda row: row["salience"], reverse=True)
    for row in body:
        assert "anomaly_id" in row
        assert row["kind"]
        assert 0.0 <= row["salience"] < 1.0
        assert isinstance(row["summary"], dict)
        assert isinstance(row["reasons"], list)
        # Detection only — no prose narration field on the anomaly itself.
        assert "text" not in row


def test_session_anomalies_404s_for_unknown_session(settings: Settings) -> None:
    response = _client(settings).get("/sessions/2099-1-race/anomalies")

    assert response.status_code == 404


# -- /anomalies/{id}/explanation ----------------------------------------------


def test_explanation_returns_valid_cited_data_for_checked_in_session(
    settings: Settings,
) -> None:
    """The fixture has two position_change anomalies with race-control
    evidence in-window — those yield grounded, cited explanations under
    `CitingNarrationModel`. Every other anomaly may fall back; both shapes
    are valid."""
    client = _client(settings)
    anomalies = client.get(f"/sessions/{SESSION_KEY}/anomalies").json()
    assert anomalies

    grounded = []
    for row in anomalies:
        response = client.get(f"/anomalies/{row['anomaly_id']}/explanation")
        assert response.status_code == 200
        body = response.json()
        assert body["anomaly_id"] == row["anomaly_id"]
        assert body["kind"] == row["kind"]
        assert body["anomaly_time"] == row["anomaly_time"]
        assert body["confidence"] in {"high", "medium", "low"}
        assert isinstance(body["cited_evidence"], list)
        if body["grounded"]:
            grounded.append(body)
            assert body["text"] != FALLBACK_TEXT
            assert body["cited_evidence"], "a grounded explanation must cite evidence"
            for item in body["cited_evidence"]:
                assert item["evidence_id"]
                assert item["kind"]
                assert item["event_time"] <= body["anomaly_time"]

    assert grounded, (
        "expected at least one grounded, cited explanation from the fixture's "
        "race-control-backed position_change anomalies"
    )


def test_explanation_404s_for_unknown_anomaly_id(settings: Settings) -> None:
    from f1_api.why import anomaly_id_path as _path
    from f1_why import Anomaly, AnomalyKind

    fake = Anomaly(
        kind=AnomalyKind.PACE_LOSS,
        lap_number=99,
        anomaly_time=datetime(2099, 1, 1, tzinfo=UTC),
        drivers=("99",),
        magnitude=1.0,
        salience=0.5,
        summary={},
        reasons=(),
    )
    fake_id = _path(KEY, fake)

    response = _client(settings).get(f"/anomalies/{fake_id}/explanation")

    assert response.status_code == 404


def test_explanation_422s_on_malformed_anomaly_id(settings: Settings) -> None:
    response = _client(settings).get("/anomalies/not-a-real-id/explanation")

    assert response.status_code == 422


def test_explanation_is_cached_and_not_regenerated(settings: Settings) -> None:
    model = CitingNarrationModel()
    store = MemoryExplanationStore()
    client = _client(settings, model=model, store=store)
    anomalies = client.get(f"/sessions/{SESSION_KEY}/anomalies").json()
    # Pick a race-control-backed anomaly so the model is actually called.
    target = next(
        row for row in anomalies if row["kind"] == "position_change" and row["lap_number"] == 17
    )

    first = client.get(f"/anomalies/{target['anomaly_id']}/explanation")
    calls_after_first = len(model.calls)
    second = client.get(f"/anomalies/{target['anomaly_id']}/explanation")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(model.calls) == calls_after_first, "cache hit must not call the model again"


# -- anomaly_id path round-trip -----------------------------------------------


def test_anomaly_id_path_round_trips() -> None:
    from f1_why import Anomaly, AnomalyKind

    anomaly = Anomaly(
        kind=AnomalyKind.UNDERCUT_RESOLVED,
        lap_number=16,
        anomaly_time=datetime(2024, 3, 2, 15, 25, 13, tzinfo=UTC),
        drivers=("4", "63"),
        magnitude=1.2,
        salience=0.7,
        summary={"predicted_side": "undercut"},
        reasons=(),
    )
    path_id = anomaly_id_path(KEY, anomaly)

    session_key, why_id = parse_anomaly_id(path_id)

    assert session_key == KEY
    assert "undercut_resolved" in why_id
    assert "4+63" in why_id


# -- Timeline: Delay Sync property + causal honesty ---------------------------


def test_explanation_envelopes_pace_on_anomaly_time_not_ahead_of_playback_clock(
    settings: Settings,
) -> None:
    """Property: across many seeded playback clocks, no explanation envelope
    whose remapped `event_time_utc` is ahead of the clock would be released
    — i.e. explanations pass the same Delay Sync gate everything else does,
    because they carry an `event_time_utc` derived from `anomaly_time` via
    the identical pacing/remap arithmetic."""
    source = HistoricalSource(HISTORICAL_ROOT)
    model = CitingNarrationModel()
    store = MemoryExplanationStore()
    fake = _FakeClock()

    envelopes = list(
        replay_envelopes(
            source,
            KEY,
            speed=1.0,
            narration_model=model,
            explanation_store=store,
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    explanations = [e for e in envelopes if e.kind == EXPLANATION_KIND]
    assert explanations, "expected explanation envelopes for the checked-in session"

    # Every explanation's remapped time must sit on the FAKE_WALL timeline
    # (never the fixture's 2024 timestamps).
    for envelope in explanations:
        assert envelope.event_time_utc.year == 2026
        assert "anomaly_id" in envelope.payload
        assert "text" in envelope.payload
        assert "cited_evidence" in envelope.payload

    times = [e.event_time_utc for e in explanations]
    assert times == sorted(times)

    rng = random.Random(20260301)
    min_t = min(times)
    max_t = max(times)
    span = (max_t - min_t).total_seconds()
    for _ in range(200):
        clock = min_t + timedelta(seconds=rng.random() * span)
        released = [e for e in explanations if e.event_time_utc <= clock]
        for envelope in released:
            assert envelope.event_time_utc <= clock


def test_explanations_never_cite_evidence_after_anomaly_time(settings: Settings) -> None:
    """Property: every cited evidence item on every explanation for the
    checked-in session has `event_time <= anomaly_time` — causal honesty
    enforced at the API boundary, not just inside `assemble_evidence`."""
    client = _client(settings)
    anomalies = client.get(f"/sessions/{SESSION_KEY}/anomalies").json()

    for row in anomalies:
        body = client.get(f"/anomalies/{row['anomaly_id']}/explanation").json()
        anomaly_time = datetime.fromisoformat(body["anomaly_time"])
        for item in body["cited_evidence"]:
            evidence_time = datetime.fromisoformat(item["event_time"])
            assert evidence_time <= anomaly_time, (
                f"evidence {item['evidence_id']} at {evidence_time} is after "
                f"anomaly {body['anomaly_id']} at {anomaly_time}"
            )


def test_explanation_envelope_payload_is_lightweight(settings: Settings) -> None:
    """WS payload carries text + evidence ids/links only — not full evidence
    bodies (lazy fetch via GET /anomalies/{id}/explanation)."""
    source = HistoricalSource(HISTORICAL_ROOT)
    fake = _FakeClock()
    envelopes = list(
        replay_envelopes(
            source,
            KEY,
            speed=0,
            narration_model=CitingNarrationModel(),
            explanation_store=MemoryExplanationStore(),
            clock=fake.now,
            sleep=fake.sleep,
            wall_clock=lambda: FAKE_WALL_START,
        )
    )
    explanation = next(e for e in envelopes if e.kind == EXPLANATION_KIND)
    payload = explanation.payload

    assert set(payload) >= {
        "anomaly_id",
        "kind",
        "lap_number",
        "drivers",
        "text",
        "confidence",
        "grounded",
        "cited_evidence",
    }
    for item in payload["cited_evidence"]:
        assert set(item) == {"evidence_id", "kind", "href"}
        assert "summary" not in item
        assert "event_time" not in item


def test_low_evidence_explanation_uses_fallback_without_model_call(
    settings: Settings,
) -> None:
    """Most fixture anomalies have no radio/race-control evidence —
    `explain` must short-circuit to FALLBACK_TEXT with zero model calls."""
    model = MockNarrationModel(
        ModelNarration(text="should never appear", cited_evidence_ids=(), no_clear_cause=False)
    )
    store = MemoryExplanationStore()
    client = _client(settings, model=model, store=store)
    anomalies = client.get(f"/sessions/{SESSION_KEY}/anomalies").json()
    # undercut_resolved anomalies are low_evidence in this fixture
    target = next(row for row in anomalies if row["kind"] == "undercut_resolved")

    response = client.get(f"/anomalies/{target['anomaly_id']}/explanation")

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == FALLBACK_TEXT
    assert body["grounded"] is False
    assert model.calls == []
