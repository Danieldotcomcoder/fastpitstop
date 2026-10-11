"""Runtime configuration, entirely from environment variables.

This is a thin read model (CLAUDE.md's Phase 3 invariant: the frontend
talks to the API only, the API never invents analytics), so there is
deliberately little to configure: where the historical backfill lives, and
optionally a database DSN for live/in-progress sessions. `F1_STORE_DSN` is
`f1_store`'s own variable (`f1_store.config.DSN_ENV_VAR`) — reused rather
than renamed, since it names the same database either way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from f1_store.config import dsn_from_env

DEFAULT_HISTORICAL_ROOT = Path("data/historical")
HISTORICAL_ROOT_ENV_VAR = "F1_API_HISTORICAL_ROOT"
CORS_ORIGINS_ENV_VAR = "F1_API_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("*",)
RADIO_FIXTURE_ENV_VAR = "F1_API_RADIO_FIXTURE"
WHY_FIXTURE_ENV_VAR = "F1_API_WHY_FIXTURE"
MAX_EXPLANATIONS_ENV_VAR = "F1_WHY_MAX_EXPLANATIONS"
DEFAULT_MAX_EXPLANATIONS = 20
"""Ceiling on story cards per session. Template narration costs nothing, so
this bounds a story's length, not a model budget; `f1_why.StoryParams`'s
rules keep most races well under it."""
FEEDBACK_ENABLED_ENV_VAR = "F1_FEEDBACK_ENABLED"
DEFAULT_LOCAL_FEEDBACK_SALT = "dev-feedback-salt"
"""Never valid in production — `f1_api.env_check.RequiredProdEnv` rejects a
deployment that still has this value for `F1_FEEDBACK_IP_SALT`."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the app factory needs. Constructed once per process
    (`get_settings`), but never a hidden global — every function that needs
    one takes it as an argument, so tests can point it at a fixture root."""

    historical_root: Path = DEFAULT_HISTORICAL_ROOT
    store_dsn: str | None = None
    cors_origins: tuple[str, ...] = field(default_factory=lambda: DEFAULT_CORS_ORIGINS)
    radio_fixture_path: Path | None = None
    """Set only by `just verify-phase4`'s Playwright harness — see
    `f1_api.radio_fixture`'s module docstring. Unset (the default, and every
    normal deployment) means the radio corpus is served from Postgres, or
    degrades to empty when no `F1_STORE_DSN` is configured, exactly like
    every other radio dependency already does."""
    why_fixture: bool = False
    """Set only by `just verify-phase5`'s Playwright harness — see
    `f1_api.why_fixture`'s module docstring. When true,
    `get_narration_model` resolves to a citing mock so E2E can assert a
    grounded explanation without a live API key. Unset (the default) keeps
    the normal Claude-or-declining-mock resolution."""
    max_explanations: int = DEFAULT_MAX_EXPLANATIONS
    """How many of a session's anomalies actually get narrated (the most
    salient ones). A real race detects a few hundred anomalies; explaining
    every one costs one model call each and, before this cap existed,
    stalled `/stream/{key}` for ~2 minutes before its first envelope. The
    cap is what makes the why-engine both affordable (CLAUDE.md's Phase 5
    budget invariant) and a readable STORY rather than an exhaustive log.
    Detection itself is uncapped — `GET /sessions/{key}/anomalies` still
    sees everything."""
    feedback_enabled: bool = True
    """Whether the beta feedback form is served at all. False drops the
    router entirely (so `POST /feedback` 404s) and relaxes production's
    `F1_TURNSTILE_SECRET` requirement, since there is nothing left to
    protect. On by default: switching it off is a deployment's choice."""
    feedback_to: str | None = None
    """Server-only destination for feedback notifications. Never expose to
    the frontend bundle."""
    feedback_ip_salt: str = DEFAULT_LOCAL_FEEDBACK_SALT
    turnstile_secret: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    git_sha: str = "dev"
    """Image build metadata (`GIT_SHA`/`BUILD_TIME` build args baked in as
    `ENV` by the Dockerfiles) — surfaced read-only via `GET /version` so the
    frontend's `/status` page can show what's actually running. Defaults are
    what any non-container run (tests, `just serve-api`) sees."""
    build_time: str = "unknown"


def get_settings() -> Settings:
    """Settings from the environment. `F1_STORE_DSN` unset is a normal,
    common state (no live session ingested yet) — never an error here; it
    just means `/sessions` and analytics endpoints only serve historical
    data until it's set."""
    historical_root = Path(os.environ.get(HISTORICAL_ROOT_ENV_VAR, str(DEFAULT_HISTORICAL_ROOT)))
    origins_raw = os.environ.get(CORS_ORIGINS_ENV_VAR)
    origins = (
        tuple(o.strip() for o in origins_raw.split(",") if o.strip())
        if origins_raw
        else (DEFAULT_CORS_ORIGINS)
    )
    radio_fixture_raw = os.environ.get(RADIO_FIXTURE_ENV_VAR)
    why_fixture_raw = os.environ.get(WHY_FIXTURE_ENV_VAR)
    max_explanations_raw = os.environ.get(MAX_EXPLANATIONS_ENV_VAR)
    try:
        max_explanations = (
            int(max_explanations_raw) if max_explanations_raw else DEFAULT_MAX_EXPLANATIONS
        )
    except ValueError:
        max_explanations = DEFAULT_MAX_EXPLANATIONS
    max_explanations = max(0, max_explanations)
    smtp_port_raw = os.environ.get("F1_SMTP_PORT", "587")
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 587
    return Settings(
        historical_root=historical_root,
        store_dsn=dsn_from_env(),
        cors_origins=origins,
        radio_fixture_path=Path(radio_fixture_raw) if radio_fixture_raw else None,
        why_fixture=bool(why_fixture_raw and why_fixture_raw.strip()),
        max_explanations=max_explanations,
        feedback_enabled=os.environ.get(FEEDBACK_ENABLED_ENV_VAR, "1").strip()
        not in {"0", "false", "False"},
        feedback_to=os.environ.get("F1_FEEDBACK_TO") or "danielshdeed1995.ds@gmail.com",
        feedback_ip_salt=os.environ.get("F1_FEEDBACK_IP_SALT") or DEFAULT_LOCAL_FEEDBACK_SALT,
        turnstile_secret=os.environ.get("F1_TURNSTILE_SECRET") or None,
        smtp_host=os.environ.get("F1_SMTP_HOST") or None,
        smtp_port=smtp_port,
        smtp_username=os.environ.get("F1_SMTP_USERNAME") or None,
        smtp_password=os.environ.get("F1_SMTP_PASSWORD") or None,
        smtp_from=os.environ.get("F1_SMTP_FROM") or None,
        smtp_use_tls=os.environ.get("F1_SMTP_USE_TLS", "1").strip() not in {"0", "false", "False"},
        git_sha=os.environ.get("GIT_SHA") or "dev",
        build_time=os.environ.get("BUILD_TIME") or "unknown",
    )


__all__ = [
    "CORS_ORIGINS_ENV_VAR",
    "DEFAULT_CORS_ORIGINS",
    "DEFAULT_HISTORICAL_ROOT",
    "DEFAULT_LOCAL_FEEDBACK_SALT",
    "DEFAULT_MAX_EXPLANATIONS",
    "FEEDBACK_ENABLED_ENV_VAR",
    "HISTORICAL_ROOT_ENV_VAR",
    "MAX_EXPLANATIONS_ENV_VAR",
    "RADIO_FIXTURE_ENV_VAR",
    "WHY_FIXTURE_ENV_VAR",
    "Settings",
    "get_settings",
]
