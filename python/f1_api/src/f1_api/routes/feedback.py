"""POST /feedback — public beta intake."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from f1_api.config import Settings, get_settings
from f1_api.feedback import (
    FeedbackIn,
    FeedbackOut,
    FeedbackRejected,
    FeedbackStore,
    MailSender,
    MemoryFeedbackStore,
    TurnstileVerifier,
    build_smtp_sender,
    build_turnstile_verifier,
    submit_feedback,
)

router = APIRouter(tags=["feedback"])

_memory_store = MemoryFeedbackStore()


def get_feedback_store(settings: Annotated[Settings, Depends(get_settings)]) -> FeedbackStore:
    # Postgres-backed store when DSN is set; memory keeps CI and local
    # static deploys working without a database.
    if settings.store_dsn:
        try:
            from f1_api.feedback_pg import PostgresFeedbackStore

            return PostgresFeedbackStore(settings.store_dsn)
        except Exception:
            return _memory_store
    return _memory_store


def get_turnstile(settings: Annotated[Settings, Depends(get_settings)]) -> TurnstileVerifier:
    return build_turnstile_verifier(settings.turnstile_secret)


def get_mail_sender(settings: Annotated[Settings, Depends(get_settings)]) -> MailSender | None:
    if not settings.smtp_host or not settings.feedback_to:
        return None
    return build_smtp_sender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username or "",
        password=settings.smtp_password or "",
        mail_from=settings.smtp_from or "noreply@localhost",
        mail_to=settings.feedback_to,
        use_tls=settings.smtp_use_tls,
    )


@router.post("/feedback", response_model=FeedbackOut)
def post_feedback(
    body: FeedbackIn,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[FeedbackStore, Depends(get_feedback_store)],
    turnstile: Annotated[TurnstileVerifier, Depends(get_turnstile)],
    send_mail: Annotated[MailSender | None, Depends(get_mail_sender)],
) -> FeedbackOut:
    ip = request.client.host if request.client else "0.0.0.0"
    try:
        return submit_feedback(
            body,
            store=store,
            ip=ip,
            ip_salt=settings.feedback_ip_salt,
            turnstile=turnstile,
            send_mail=send_mail,
        )
    except FeedbackRejected as exc:
        # Rate limits return a friendly body; FastAPI still needs a status.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
