"""Fail-loud production startup validation (Prompt 2 of CLAUDE.md's Phase 3:
"a container that boots into a broken state is worse than one that refuses
to boot"). Deliberately separate from `f1_api.config.Settings` — that
dataclass is constructed directly by ~20 test modules via
`app.dependency_overrides`, and every field there is legitimately optional
in dev/CI (no live DB, no SMTP, no live session yet). `RequiredProdEnv` only
ever runs when `F1_ENV=production` (set by `compose.prod.yml`, never by
tests or `just serve-api`), so it can be strict without touching that path.
"""

from __future__ import annotations

import os

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from f1_api.config import DEFAULT_LOCAL_FEEDBACK_SALT

PROD_ENV_FLAG = "F1_ENV"
PROD_ENV_VALUE = "production"


class RequiredProdEnv(BaseSettings):
    """The env vars a real deployment must set. SMTP is validated as a
    group — either all four of host/username/password/from are set, or
    none are (feedback then just skips emailing, a valid non-prod-only
    state), never a partial config that silently drops notifications."""

    model_config = SettingsConfigDict(extra="ignore")

    f1_store_dsn: str = Field(..., alias="F1_STORE_DSN")
    f1_feedback_ip_salt: str = Field(..., alias="F1_FEEDBACK_IP_SALT")
    f1_turnstile_secret: str = Field(..., alias="F1_TURNSTILE_SECRET")
    f1_smtp_host: str | None = Field(None, alias="F1_SMTP_HOST")
    f1_smtp_username: str | None = Field(None, alias="F1_SMTP_USERNAME")
    f1_smtp_password: str | None = Field(None, alias="F1_SMTP_PASSWORD")
    f1_smtp_from: str | None = Field(None, alias="F1_SMTP_FROM")

    @field_validator("f1_feedback_ip_salt")
    @classmethod
    def _salt_not_dev_default(cls, value: str) -> str:
        if value == DEFAULT_LOCAL_FEEDBACK_SALT:
            raise ValueError(
                f"F1_FEEDBACK_IP_SALT is still the dev default ({DEFAULT_LOCAL_FEEDBACK_SALT!r}) "
                "— set a real secret for production"
            )
        return value

    @model_validator(mode="after")
    def _smtp_all_or_nothing(self) -> RequiredProdEnv:
        smtp_fields = {
            "F1_SMTP_HOST": self.f1_smtp_host,
            "F1_SMTP_USERNAME": self.f1_smtp_username,
            "F1_SMTP_PASSWORD": self.f1_smtp_password,
            "F1_SMTP_FROM": self.f1_smtp_from,
        }
        set_names = [name for name, value in smtp_fields.items() if value]
        if set_names and len(set_names) != len(smtp_fields):
            missing = sorted(set(smtp_fields) - set(set_names))
            raise ValueError(
                f"partial SMTP config: {sorted(set_names)} set but {missing} missing "
                "— set all four (feedback emails) or none (feedback still stored, no email)"
            )
        return self


def validate_production_env() -> None:
    """Raise `pydantic.ValidationError` (uncaught — the process should not
    start) if `F1_ENV=production` and a required var is missing/invalid.
    A no-op in every other environment."""
    if os.environ.get(PROD_ENV_FLAG) != PROD_ENV_VALUE:
        return
    RequiredProdEnv()  # type: ignore[call-arg] — values come from the environment, not kwargs


__all__ = ["PROD_ENV_FLAG", "PROD_ENV_VALUE", "RequiredProdEnv", "validate_production_env"]
