"""The WebSocket `mode=replay` path streams a full session end-to-end with
monotonic `event_time_utc` and well-formed envelopes; kernel snapshots
appear and carry confidence — the Phase 3.1 test requirement from
CLAUDE.md's prompt.

`mode=live` is deliberately NOT tested here: it needs a live-provenance
session in the store, and per docs/PHASE0.md the live path itself is
exercised for real on race weekends rather than unit-tested — the same
reasoning `f1_ingest`'s own connection layer follows.
"""

from __future__ import annotations

import pytest
from api_fixture import SESSION_KEY
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from f1_api.schemas import StreamEnvelope


def _collect(client: TestClient, url: str) -> list[StreamEnvelope]:
    envelopes: list[StreamEnvelope] = []
    with client.websocket_connect(url) as websocket:
        while True:
            try:
                data = websocket.receive_json()
            except Exception:
                break
            envelopes.append(StreamEnvelope.model_validate(data))
    return envelopes


def test_replay_stream_end_to_end(client: TestClient) -> None:
    envelopes = _collect(client, f"/stream/{SESSION_KEY}?mode=replay&speed=0")

    assert envelopes, "expected at least one envelope"

    times = [envelope.event_time_utc for envelope in envelopes]
    assert times == sorted(times), "event_time_utc must never go backwards"

    kinds = {envelope.kind for envelope in envelopes}
    assert "lap_completed" in kinds
    assert "stint_started" in kinds
    assert "kernel_snapshot" in kinds

    # Synthetic kinds (kernel_snapshot / radio_clip / explanation) carry
    # their own payload shapes — only DomainEvent envelopes have session_key.
    synthetic = {"kernel_snapshot", "radio_clip", "explanation"}
    domain_envelopes = [e for e in envelopes if e.kind not in synthetic]
    assert all("session_key" in e.payload for e in domain_envelopes), (
        "DomainEvent envelopes must carry session_key in their payload"
    )


def test_replay_stream_kernel_snapshots_carry_confidence(client: TestClient) -> None:
    envelopes = _collect(client, f"/stream/{SESSION_KEY}?mode=replay&speed=0")
    snapshots = [e for e in envelopes if e.kind == "kernel_snapshot"]

    assert snapshots, "expected at least one kernel_snapshot envelope"
    for snapshot in snapshots:
        assert "decision_lap" in snapshot.payload
        crossovers = snapshot.payload["crossovers"]
        assert crossovers, "a snapshot is only emitted when it has crossovers"
        for crossover in crossovers:
            assert crossover["confidence"] in {"high", "medium", "low"}
            assert crossover["window"] in {"open", "opens_later", "closed", "unknown"}


def test_replay_stream_excludes_telemetry_by_default(client: TestClient) -> None:
    """SESSION_KEY (2024-1-race) is the fixture race with a car_telemetry
    table (api_fixture.py) — the regression case for docs/PHASE3.md's
    WebSocket section: telemetry must not ride the default stream."""
    envelopes = _collect(client, f"/stream/{SESSION_KEY}?mode=replay&speed=0")

    assert envelopes, "expected at least one envelope"
    assert all(envelope.kind != "car_telemetry" for envelope in envelopes)


def test_replay_stream_includes_telemetry_when_requested(client: TestClient) -> None:
    envelopes = _collect(
        client, f"/stream/{SESSION_KEY}?mode=replay&speed=0&include_telemetry=true"
    )

    assert any(envelope.kind == "car_telemetry" for envelope in envelopes)


def test_replay_stream_unknown_session_closes(client: TestClient) -> None:
    # The server closes immediately with a 404-equivalent close code; the
    # first receive should observe the disconnect rather than data.
    with (
        client.websocket_connect("/stream/2099-1-race?mode=replay") as websocket,
        pytest.raises(WebSocketDisconnect),
    ):
        websocket.receive_json()
