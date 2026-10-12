"""Operator email: the one way the services beside the API tell someone
something went wrong (`f1_api.watchdog`, `f1_api.post_session`).

Sent only when SMTP is configured (the same `F1_SMTP_*` settings the
feedback form uses), to `F1_ALERT_TO`, falling back to `F1_FEEDBACK_TO`.
Callers log the alert themselves first: the log is the record, the email a
nudge, and a failed send never raises.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from f1_api.config import Settings

_log = logging.getLogger("f1_api.alerts")


def send_alert(settings: Settings, subject: str, body: str) -> bool:
    """Email `body` under `[FastPitStop] subject`. True when it was sent."""
    recipient = os.environ.get("F1_ALERT_TO") or settings.feedback_to
    if not settings.smtp_host or not recipient:
        return False
    message = EmailMessage()
    message["Subject"] = f"[FastPitStop] {subject}"
    message["From"] = settings.smtp_from or recipient
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    except Exception:
        _log.warning("could not email the alert; it is in the log", exc_info=True)
        return False
    return True
