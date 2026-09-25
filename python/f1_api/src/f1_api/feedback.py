"""Feedback intake — validate, persist, optionally notify by email.

Postgres (or an in-memory store in tests) is the source of truth. SMTP is a
best-effort notification: a failed send never drops the row.
"""

from __future__ import annotations

import hashlib
import re
import smtplib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any, Protocol

from pydantic import BaseModel, Field

MAX_BODY_CHARS = 2000
MAX_EMAIL_CHARS = 254
MIN_SUBMIT_SECONDS = 3.0
RATE_LIMIT_PER_IP_HOUR = 3
RATE_LIMIT_GLOBAL_DAY = 200
HONEYPOT_FIELD = "company_website"

_HEADER_UNSAFE = re.compile(r"[\r\n]")


class FeedbackIn(BaseModel):
    what_doing: str = Field(max_length=MAX_BODY_CHARS)
    what_happened: str = Field(max_length=MAX_BODY_CHARS)
    what_expected: str = Field(max_length=MAX_BODY_CHARS)
    reply_email: str | None = Field(default=None, max_length=MAX_EMAIL_CHARS)
    route: str = Field(max_length=500)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    """Client-shown context (viewport, version, delay, UA) — stored as JSON."""
    form_started_at_ms: float
    """Client clock when the form mounted — used for the min-time check."""
    turnstile_token: str = ""
    company_website: str = ""
    """Honeypot — must stay empty. Named like a real field so bots fill it."""


class FeedbackOut(BaseModel):
    ok: bool = True
    message: str = "Thanks — we got it."


class FeedbackRejected(Exception):  # noqa: N818 — see below
    """Friendly validation / rate-limit failure — routes map to HTTP 200/400
    with a plain message rather than a 429 blob.

    Deliberately not `...Error`-suffixed (ruff's N818): this is a normal,
    expected outcome of a public form submission, not a program error, and
    the name is already part of this module's public surface."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value)


def reject_header_injection(value: str, field_name: str) -> str:
    if _HEADER_UNSAFE.search(value):
        raise FeedbackRejected(f"{field_name} cannot contain line breaks.")
    return value


def normalize_body(value: str, field_name: str) -> str:
    cleaned = strip_html(value).strip()
    if not cleaned:
        raise FeedbackRejected(f"Please fill in {field_name}.")
    if len(cleaned) > MAX_BODY_CHARS:
        raise FeedbackRejected(f"{field_name} is too long (max {MAX_BODY_CHARS} characters).")
    return cleaned


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = strip_html(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_EMAIL_CHARS:
        raise FeedbackRejected("Email is too long.")
    reject_header_injection(cleaned, "email")
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise FeedbackRejected("That email does not look valid.")
    return cleaned


def hash_ip(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    id: int
    created_at: datetime
    ip_hash: str
    route: str
    what_doing: str
    what_happened: str
    what_expected: str
    reply_email: str | None
    diagnostics: dict[str, Any]
    email_sent: bool = False
    email_error: str | None = None


class FeedbackStore(Protocol):
    def insert(self, record: FeedbackRecord) -> FeedbackRecord: ...

    def mark_email(self, record_id: int, *, sent: bool, error: str | None) -> None: ...

    def count_since(self, *, since: datetime, ip_hash: str | None = None) -> int: ...


@dataclass
class MemoryFeedbackStore:
    """In-memory sink for tests — same shape as the Postgres store."""

    _rows: list[FeedbackRecord] = field(default_factory=list[FeedbackRecord])
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1

    def records(self) -> list[FeedbackRecord]:
        """A snapshot of everything stored — the read a test needs, so it
        never has to reach into `_rows`."""
        with self._lock:
            return list(self._rows)

    def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        with self._lock:
            stored = FeedbackRecord(
                id=self._next_id,
                created_at=record.created_at,
                ip_hash=record.ip_hash,
                route=record.route,
                what_doing=record.what_doing,
                what_happened=record.what_happened,
                what_expected=record.what_expected,
                reply_email=record.reply_email,
                diagnostics=dict(record.diagnostics),
                email_sent=record.email_sent,
                email_error=record.email_error,
            )
            self._next_id += 1
            self._rows.append(stored)
            return stored

    def mark_email(self, record_id: int, *, sent: bool, error: str | None) -> None:
        with self._lock:
            for i, row in enumerate(self._rows):
                if row.id == record_id:
                    self._rows[i] = FeedbackRecord(
                        id=row.id,
                        created_at=row.created_at,
                        ip_hash=row.ip_hash,
                        route=row.route,
                        what_doing=row.what_doing,
                        what_happened=row.what_happened,
                        what_expected=row.what_expected,
                        reply_email=row.reply_email,
                        diagnostics=row.diagnostics,
                        email_sent=sent,
                        email_error=error,
                    )
                    return

    def count_since(self, *, since: datetime, ip_hash: str | None = None) -> int:
        with self._lock:
            return sum(
                1
                for row in self._rows
                if row.created_at >= since and (ip_hash is None or row.ip_hash == ip_hash)
            )


TurnstileVerifier = Callable[[str, str | None], bool]
MailSender = Callable[[FeedbackRecord], None]


def build_turnstile_verifier(secret: str | None) -> TurnstileVerifier:
    if not secret:
        # Dev / CI: accept any token (including empty) so the form works
        # without Cloudflare keys. Production must set the secret.
        return lambda token, remote_ip=None: True

    def verify(token: str, remote_ip: str | None = None) -> bool:
        if not token.strip():
            return False
        import urllib.parse
        import urllib.request

        payload = urllib.parse.urlencode(
            {
                "secret": secret,
                "response": token,
                **({"remoteip": remote_ip} if remote_ip else {}),
            }
        ).encode()
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                import json

                body = json.loads(resp.read().decode())
                return bool(body.get("success"))
        except Exception:
            return False

    return verify


def build_smtp_sender(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    mail_from: str,
    mail_to: str,
    use_tls: bool = True,
) -> MailSender:
    def send(record: FeedbackRecord) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[FastPitStop feedback] {record.route}"
        msg["From"] = mail_from
        msg["To"] = mail_to
        # Submitter address stays in the body until validated — never Reply-To.
        lines = [
            f"Route: {record.route}",
            f"Reply email (unvalidated): {record.reply_email or '(none)'}",
            "",
            "What were you doing:",
            record.what_doing,
            "",
            "What happened:",
            record.what_happened,
            "",
            "What did you expect:",
            record.what_expected,
            "",
            f"Diagnostics: {record.diagnostics}",
            f"Submission id: {record.id}",
        ]
        msg.set_content("\n".join(lines))
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)

    return send


def submit_feedback(
    payload: FeedbackIn,
    *,
    store: FeedbackStore,
    ip: str,
    ip_salt: str,
    turnstile: TurnstileVerifier,
    send_mail: MailSender | None,
    now: datetime | None = None,
    wall_now_ms: float | None = None,
) -> FeedbackOut:
    now = now or datetime.now(UTC)
    wall_now_ms = wall_now_ms if wall_now_ms is not None else time.time() * 1000

    if payload.company_website.strip():
        raise FeedbackRejected("Could not accept this submission.")

    elapsed_s = (wall_now_ms - payload.form_started_at_ms) / 1000.0
    if elapsed_s < MIN_SUBMIT_SECONDS:
        raise FeedbackRejected("That was too fast — please take a moment and try again.")

    if not turnstile(payload.turnstile_token, ip):
        raise FeedbackRejected("Could not verify you are human. Refresh and try again.")

    what_doing = normalize_body(payload.what_doing, "what you were doing")
    what_happened = normalize_body(payload.what_happened, "what happened")
    what_expected = normalize_body(payload.what_expected, "what you expected")
    reply_email = normalize_email(payload.reply_email)
    route = reject_header_injection(strip_html(payload.route).strip() or "/", "route")
    if len(route) > 500:
        raise FeedbackRejected("Route is too long.")

    ip_digest = hash_ip(ip, ip_salt)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    if store.count_since(since=hour_ago, ip_hash=ip_digest) >= RATE_LIMIT_PER_IP_HOUR:
        raise FeedbackRejected(
            "You've sent a few notes already this hour — try again a little later.",
            status_code=429,
        )
    if store.count_since(since=day_ago) >= RATE_LIMIT_GLOBAL_DAY:
        raise FeedbackRejected(
            "We've hit today's feedback limit — please try again tomorrow.",
            status_code=429,
        )

    record = store.insert(
        FeedbackRecord(
            id=0,
            created_at=now,
            ip_hash=ip_digest,
            route=route,
            what_doing=what_doing,
            what_happened=what_happened,
            what_expected=what_expected,
            reply_email=reply_email,
            diagnostics=dict(payload.diagnostics),
        )
    )

    if send_mail is not None:
        try:
            send_mail(record)
            store.mark_email(record.id, sent=True, error=None)
        except Exception as exc:
            store.mark_email(record.id, sent=False, error=str(exc)[:500])

    return FeedbackOut()


__all__ = [
    "HONEYPOT_FIELD",
    "MAX_BODY_CHARS",
    "MAX_EMAIL_CHARS",
    "MIN_SUBMIT_SECONDS",
    "RATE_LIMIT_GLOBAL_DAY",
    "RATE_LIMIT_PER_IP_HOUR",
    "FeedbackIn",
    "FeedbackOut",
    "FeedbackRecord",
    "FeedbackRejected",
    "FeedbackStore",
    "MailSender",
    "MemoryFeedbackStore",
    "TurnstileVerifier",
    "build_smtp_sender",
    "build_turnstile_verifier",
    "hash_ip",
    "submit_feedback",
]
