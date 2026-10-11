"""`f1-api` — run the service with uvicorn. A dev convenience; a real
deployment invokes uvicorn/gunicorn directly against `f1_api.main:app`.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime

import uvicorn

from f1_api.config import get_settings
from f1_api.env_check import PROD_ENV_FLAG, PROD_ENV_VALUE, validate_production_env
from f1_api.logging_config import LOG_CONFIG, configure_logging
from f1_api.routes.schedule import get_schedule_cache


def main() -> int:
    parser = argparse.ArgumentParser(prog="f1-api", description="Run the F1 Companion API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on source changes.")
    args = parser.parse_args()

    configure_logging()
    # no-op unless F1_ENV=production — fails loudly, before uvicorn ever starts.
    validate_production_env()
    if os.environ.get(PROD_ENV_FLAG) == PROD_ENV_VALUE:
        # The calendar is fetched in the background before the first request,
        # so the first home page after a deploy doesn't wait on FastF1. Never
        # elsewhere: tests and CI must not reach the network unasked.
        get_schedule_cache(get_settings()).warm(datetime.now(UTC).year)

    uvicorn.run(
        "f1_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=LOG_CONFIG,
        # A viewer joining a live race mid-session first downloads the race
        # so far; the keepalive ping queues behind that download, so on a
        # slow phone connection uvicorn's default 20 s timeout dropped the
        # viewer, who reconnected and started again. A dead peer is still
        # detected, just within a minute instead of 20 seconds.
        ws_ping_interval=20.0,
        ws_ping_timeout=60.0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
