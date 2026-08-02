"""Phase 5.5's env-var-driven citing-mock mode (`f1_api.why_fixture`,
`F1_API_WHY_FIXTURE`) — the seam `just verify-phase5`'s Playwright harness
relies on to get grounded, cited explanations out of a REAL running
`f1_api` process with no live API key. `test_why.py` already covers the
REST/WS surface against `app.dependency_overrides`; this file proves the
env-var wiring those overrides stand in for during a pytest run.
"""

from __future__ import annotations

import pytest
from api_fixture import HISTORICAL_ROOT, SESSION_KEY
from fastapi.testclient import TestClient

from f1_api.config import WHY_FIXTURE_ENV_VAR, Settings, get_settings
from f1_api.main import create_app
from f1_api.why import get_explanation_store, get_narration_model
from f1_api.why_fixture import GROUNDED_FIXTURE_TEXT, CitingNarrationModel
from f1_why import FALLBACK_TEXT, MemoryExplanationStore


@pytest.fixture(autouse=True)
def _reset_narration_singleton() -> None:
    """`get_narration_model` is a process-level lazy singleton — clear it
    between tests so an earlier resolution can't leak into a later env
    change in this file."""
    import f1_api.why as why_mod

    why_mod._narration_model = None  # test seam: clear process singleton
    yield
    why_mod._narration_model = None


def test_settings_reads_why_fixture_flag_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WHY_FIXTURE_ENV_VAR, "1")

    settings = get_settings()

    assert settings.why_fixture is True


def test_get_narration_model_resolves_citing_mock_when_fixture_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WHY_FIXTURE_ENV_VAR, "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    model = get_narration_model()

    assert isinstance(model, CitingNarrationModel)


def test_fixture_mode_yields_grounded_and_fallback_explanations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Against the checked-in kernel fixture: race-control-backed
    position_change anomalies are grounded; at least one other kind falls
    back — the two shapes `just verify-phase5` asserts in the browser."""
    monkeypatch.setenv(WHY_FIXTURE_ENV_VAR, "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    app = create_app(Settings(historical_root=HISTORICAL_ROOT, why_fixture=True))
    # Force the singleton path the real process uses (no dependency override
    # for the model) while keeping a fresh store so this test can't see
    # explanations cached by a sibling.
    app.dependency_overrides[get_explanation_store] = lambda: MemoryExplanationStore()
    client = TestClient(app)

    anomalies = client.get(f"/sessions/{SESSION_KEY}/anomalies").json()
    assert anomalies

    grounded: list[dict[str, object]] = []
    fallbacks: list[dict[str, object]] = []
    for row in anomalies:
        body = client.get(f"/anomalies/{row['anomaly_id']}/explanation").json()
        if body["grounded"]:
            grounded.append(body)
            assert body["text"] == GROUNDED_FIXTURE_TEXT
            assert body["cited_evidence"]
        else:
            fallbacks.append(body)
            assert body["text"] == FALLBACK_TEXT
            assert body["confidence"] == "low"

    assert grounded, "expected at least one grounded explanation under the citing fixture"
    assert fallbacks, "expected at least one honest fallback under the citing fixture"
