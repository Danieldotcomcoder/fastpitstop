"""Feedback intake — rate limits, honeypot, header injection, email fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from f1_api.config import Settings
from f1_api.feedback import (
    FeedbackIn,
    FeedbackRecord,
    FeedbackRejected,
    MemoryFeedbackStore,
    RATE_LIMIT_PER_IP_HOUR,
    submit_feedback,
)
from f1_api.main import create_app
from f1_api.routes import feedback as feedback_routes


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "what_doing": "Watching the live page",
        "what_happened": "Gap numbers jumped",
        "what_expected": "Smooth updates",
        "reply_email": "fan@example.com",
        "route": "/live",
        "diagnostics": {"version": "0.1.0-beta", "viewport": "390x844"},
        "form_started_at_ms": 1_000_000.0,
        "turnstile_token": "ok",
        "company_website": "",
    }
    base.update(overrides)
    return base


def test_submit_persists_when_email_fails() -> None:
    store = MemoryFeedbackStore()
    sent: list[FeedbackRecord] = []

    def boom(record: FeedbackRecord) -> None:
        sent.append(record)
        raise RuntimeError("smtp down")

    out = submit_feedback(
        FeedbackIn.model_validate(_payload()),
        store=store,
        ip="1.2.3.4",
        ip_salt="salt",
        turnstile=lambda token, remote_ip=None: True,
        send_mail=boom,
        now=datetime(2026, 7, 31, tzinfo=UTC),
        wall_now_ms=1_000_000.0 + 5_000,
    )
    assert out.ok
    assert store.count_since(since=datetime(2026, 7, 30, tzinfo=UTC)) == 1
    assert store._rows[0].email_sent is False
    assert store._rows[0].email_error is not None
    assert len(sent) == 1


def test_honeypot_rejected() -> None:
    store = MemoryFeedbackStore()
    with pytest.raises(FeedbackRejected):
        submit_feedback(
            FeedbackIn.model_validate(_payload(company_website="http://spam")),
            store=store,
            ip="1.2.3.4",
            ip_salt="salt",
            turnstile=lambda token, remote_ip=None: True,
            send_mail=None,
            wall_now_ms=1_000_000.0 + 5_000,
        )
    assert store.count_since(since=datetime(2020, 1, 1, tzinfo=UTC)) == 0


def test_header_injection_in_email_rejected() -> None:
    store = MemoryFeedbackStore()
    with pytest.raises(FeedbackRejected, match="line breaks"):
        submit_feedback(
            FeedbackIn.model_validate(_payload(reply_email="evil@x.com\nBcc: victim@x.com")),
            store=store,
            ip="1.2.3.4",
            ip_salt="salt",
            turnstile=lambda token, remote_ip=None: True,
            send_mail=None,
            wall_now_ms=1_000_000.0 + 5_000,
        )


def test_too_fast_rejected() -> None:
    store = MemoryFeedbackStore()
    with pytest.raises(FeedbackRejected, match="too fast"):
        submit_feedback(
            FeedbackIn.model_validate(_payload()),
            store=store,
            ip="1.2.3.4",
            ip_salt="salt",
            turnstile=lambda token, remote_ip=None: True,
            send_mail=None,
            wall_now_ms=1_000_000.0 + 500,
        )


def test_rate_limit_per_ip() -> None:
    store = MemoryFeedbackStore()
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    for i in range(RATE_LIMIT_PER_IP_HOUR):
        submit_feedback(
            FeedbackIn.model_validate(_payload(what_doing=f"attempt {i}")),
            store=store,
            ip="9.9.9.9",
            ip_salt="salt",
            turnstile=lambda token, remote_ip=None: True,
            send_mail=None,
            now=now,
            wall_now_ms=1_000_000.0 + 5_000,
        )
    with pytest.raises(FeedbackRejected, match="this hour"):
        submit_feedback(
            FeedbackIn.model_validate(_payload(what_doing="one more")),
            store=store,
            ip="9.9.9.9",
            ip_salt="salt",
            turnstile=lambda token, remote_ip=None: True,
            send_mail=None,
            now=now,
            wall_now_ms=1_000_000.0 + 5_000,
        )


def test_http_endpoint_friendly_error(tmp_path) -> None:
    settings = Settings(historical_root=tmp_path)
    store = MemoryFeedbackStore()
    app = create_app(settings)
    app.dependency_overrides[feedback_routes.get_feedback_store] = lambda: store
    app.dependency_overrides[feedback_routes.get_turnstile] = lambda: (
        lambda token, remote_ip=None: True
    )
    app.dependency_overrides[feedback_routes.get_mail_sender] = lambda: None
    client = TestClient(app)
    response = client.post(
        "/feedback",
        json=_payload(company_website="bot"),
    )
    assert response.status_code == 400
    assert "detail" in response.json()
