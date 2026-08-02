"""`RequiredProdEnv` — the container deploy's fail-loud startup validator.
Only ever active when `F1_ENV=production` (`validate_production_env`'s own
no-op-by-default contract), so these tests construct `RequiredProdEnv`
directly rather than going through `main()`/uvicorn."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from f1_api.config import DEFAULT_LOCAL_FEEDBACK_SALT
from f1_api.env_check import PROD_ENV_FLAG, RequiredProdEnv, validate_production_env

_FULL_ENV = {
    "F1_STORE_DSN": "postgresql://pitwall:pitwall@db:5432/pitwall",
    "F1_FEEDBACK_IP_SALT": "a-real-secret",
    "F1_TURNSTILE_SECRET": "ts-secret",
}


def _build() -> RequiredProdEnv:
    """`BaseSettings` fields are read from the environment at construction
    time, but pyright's synthesized `__init__` (strict mode) still sees them
    as required keyword args — the single ignore lives here rather than at
    every call site."""
    return RequiredProdEnv()  # type: ignore[call-arg]


def test_all_required_vars_present(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        monkeypatch.setenv(key, value)
    settings = _build()
    assert settings.f1_store_dsn == _FULL_ENV["F1_STORE_DSN"]


def test_missing_store_dsn_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        if key != "F1_STORE_DSN":
            monkeypatch.setenv(key, value)
    monkeypatch.delenv("F1_STORE_DSN", raising=False)
    with pytest.raises(ValidationError, match="F1_STORE_DSN"):
        _build()


def test_dev_default_salt_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("F1_FEEDBACK_IP_SALT", DEFAULT_LOCAL_FEEDBACK_SALT)
    with pytest.raises(ValidationError, match="dev default"):
        _build()


def test_partial_smtp_config_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("F1_SMTP_HOST", "smtp.example.com")
    # username/password/from left unset
    with pytest.raises(ValidationError, match="partial SMTP"):
        _build()


def test_full_smtp_config_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("F1_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("F1_SMTP_USERNAME", "bot")
    monkeypatch.setenv("F1_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("F1_SMTP_FROM", "noreply@example.com")
    _build()  # does not raise


def test_no_smtp_config_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _FULL_ENV.items():
        monkeypatch.setenv(key, value)
    _build()  # does not raise — feedback just skips emailing


def test_validate_production_env_noop_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROD_ENV_FLAG, raising=False)
    monkeypatch.delenv("F1_STORE_DSN", raising=False)
    validate_production_env()  # does not raise even though nothing is set


def test_validate_production_env_raises_when_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROD_ENV_FLAG, "production")
    monkeypatch.delenv("F1_STORE_DSN", raising=False)
    with pytest.raises(ValidationError):
        validate_production_env()
