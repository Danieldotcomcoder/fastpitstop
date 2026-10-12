"""`openapi.json` is generated and matches the pydantic models — i.e. the
committed schema (`python/f1_api/openapi.json`, the Phase 3 frontend's
typed-client source per CLAUDE.md's prompt) is exactly what the app
currently generates. Regenerate after any response-model change:

    uv run python python/f1_api/tools/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from f1_api.main import create_app

COMMITTED_PATH = Path(__file__).resolve().parents[1] / "openapi.json"

EXPECTED_PATHS = {
    "/health",
    "/healthz",
    "/readyz",
    "/time",
    "/version",
    "/sessions",
    "/sessions/{key}",
    "/sessions/{key}/laps",
    "/sessions/{key}/degradation",
    "/sessions/{key}/analysis",
    "/sessions/{key}/circuit",
    "/sessions/{key}/telemetry/{driver}",
    "/sessions/{key}/radio",
    "/sessions/{key}/anomalies",
    "/sessions/{key}/story",
    "/schedule",
    "/schedule/next",
    "/standings",
    "/circuits",
    "/circuits/{circuit_id}",
    "/anomalies/{anomaly_id}/explanation",
    "/radio/search",
    "/radio/{clip_id}",
    "/feedback",
}


def test_openapi_has_every_documented_endpoint() -> None:
    schema = create_app().openapi()
    assert set(schema["paths"]) == EXPECTED_PATHS


def test_openapi_schema_matches_committed_file() -> None:
    current = create_app().openapi()
    assert COMMITTED_PATH.is_file(), (
        f"{COMMITTED_PATH} is missing — run "
        "`uv run python python/f1_api/tools/export_openapi.py` and commit it"
    )
    committed = json.loads(COMMITTED_PATH.read_text(encoding="utf-8"))
    assert current == committed, (
        "openapi.json is stale relative to the current pydantic models — "
        "regenerate with `uv run python python/f1_api/tools/export_openapi.py`"
    )
