"""FastAPI app factory. `create_app` wires the two routers (REST + the
`/stream` WebSocket), the health routes (`/health`, plus the container-deploy
`/healthz` liveness / `/readyz` readiness / `/version` build-info trio), CORS
for local dev, and structured request logging — nothing else lives here;
every response shape comes from `f1_api.schemas` and every read from
`f1_api.routes`/`f1_api.stream` (CLAUDE.md's Phase 3 invariant: this is a
thin read model, not a second home for kernel logic).

Routes and middleware are module-level, not closures inside `create_app`
(the tempting FastAPI-factory idiom): a locally-defined, decorator-
registered function whose name is never read again afterward is dead code
by every static analyzer's reckoning even though the decorator's side
effect (route registration) is very much alive, so keeping them at module
scope is what keeps this file pyright-strict-clean without a suppression
comment.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from f1_api.config import Settings, get_settings
from f1_api.routes.circuits import router as circuits_router
from f1_api.routes.feedback import router as feedback_router
from f1_api.routes.radio import router as radio_router
from f1_api.routes.schedule import router as schedule_router
from f1_api.routes.sessions import router as sessions_router
from f1_api.routes.standings import router as standings_router
from f1_api.routes.why import router as why_router
from f1_api.schemas import HealthOut, ReadyOut, ServerTimeOut, VersionOut
from f1_api.stream.websocket import router as stream_router

logger = logging.getLogger("f1_api")

REQUEST_ID_HEADER = "X-Request-Id"
READYZ_CONNECT_TIMEOUT_S = 3
"""Short on purpose — a readiness probe that itself hangs for the OS's own
TCP timeout defeats the point of having one."""

API_TITLE = "F1 Companion API"
API_DESCRIPTION = (
    "Unofficial fan project, not associated with Formula 1. The read model "
    "bridging the analytics kernel and event store to the frontend — REST "
    "for point-in-time reads, a WebSocket envelope stream for live/replay."
)
API_VERSION = "0.0.1"

health_router = APIRouter(tags=["health"])


@health_router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut()


@health_router.get("/healthz", response_model=HealthOut)
def healthz() -> HealthOut:
    """Liveness only — no DB call, so a slow/unreachable database never
    makes an orchestrator think the process itself is dead."""
    return HealthOut()


@health_router.get("/readyz", response_model=ReadyOut)
def readyz(settings: Annotated[Settings, Depends(get_settings)]) -> ReadyOut:
    """Readiness — actually touches the database. 503 (not a 200 with a
    false field) when it can't, so a load balancer/orchestrator reads this
    the same way as any other liveness/readiness split."""
    if not settings.store_dsn:
        raise HTTPException(status_code=503, detail="database not configured")
    try:
        with psycopg.connect(
            settings.store_dsn, autocommit=True, connect_timeout=READYZ_CONNECT_TIMEOUT_S
        ) as conn:
            row = conn.execute("SELECT max(ts_utc) FROM events").fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}") from None
    last_ingest_at = row[0] if row else None
    return ReadyOut(last_ingest_at=last_ingest_at)


@health_router.get("/time", response_model=ServerTimeOut)
async def server_time() -> ServerTimeOut:
    """The server's clock, so a viewer can correct their own.

    Every `event_time_utc` the stream sends is this machine's clock (the
    live ingest stamps events on receipt; replay stamps each envelope as it
    is sent), and Delay Sync compares it with the viewer's device clock. A
    device a minute fast would release everything a minute early, a spoiler
    for a viewer on a broadcast delay; one a minute slow would show "live"
    a minute late. The frontend measures its offset from this, by round
    trip, and runs Delay Sync on the corrected clock.

    `async` so the reading is taken on the event loop as soon as the request
    lands, never queued behind sync routes in the threadpool; a late reading
    is what made the frontend think the server was ahead (it now uses a
    lower bound, `$lib/delay-sync/server-clock`, so lateness can only delay
    a release, but a prompt reading keeps that delay small)."""
    return ServerTimeOut(server_time_ms=int(time.time() * 1000))


@health_router.get("/version", response_model=VersionOut)
def version(settings: Annotated[Settings, Depends(get_settings)]) -> VersionOut:
    return VersionOut(git_sha=settings.git_sha, build_time=settings.build_time)


async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={"request_id": request_id},
    )
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Passing `settings` (rather than letting every request
    read the environment via `get_settings`) is what lets tests point the
    whole app at a fixture historical root without touching `os.environ`."""
    app = FastAPI(title=API_TITLE, description=API_DESCRIPTION, version=API_VERSION)

    resolved_settings = settings if settings is not None else get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests)

    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(schedule_router)
    app.include_router(circuits_router)
    app.include_router(standings_router)
    app.include_router(radio_router)
    app.include_router(why_router)
    # Dropped entirely rather than served-and-failing when disabled: a
    # route that exists but rejects every submission is worse than a 404.
    if resolved_settings.feedback_enabled:
        app.include_router(feedback_router)
    app.include_router(stream_router)

    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    return app


app = create_app()

__all__ = ["API_DESCRIPTION", "API_TITLE", "API_VERSION", "app", "create_app"]
