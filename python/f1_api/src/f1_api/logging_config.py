"""Structured JSON logging for the container deploy (Prompt 2 of CLAUDE.md's
Phase 3: "structured JSON logs to stdout... no print statements"). A single
`JsonFormatter` covers both `f1_api`'s own logger and uvicorn's — `cli.py`
calls `configure_logging()` before `uvicorn.run(...)` and passes the same
formatter into uvicorn's own `log_config` so access logs and app logs come
out as the same one-object-per-line shape a log aggregator (or plain `jq`
over `docker compose logs`) can parse uniformly.

`request_id` is optional on any given record — only `main._log_requests`
attaches it (via `extra={"request_id": ...}`) — so this formatter must not
assume every record has one.
"""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

_RESERVED_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: `timestamp`, `level`, `logger`, `message`,
    plus any `extra=` fields a caller attached (e.g. `request_id`)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": JsonFormatter}},
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "loggers": {
        "f1_api": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["default"], "level": "INFO"},
}


def configure_logging() -> None:
    """Apply `LOG_CONFIG` to the current process. Idempotent — safe to call
    once from `cli.main()`; `uvicorn.run`'s own `log_config=LOG_CONFIG` picks
    up the same dict so its access/error loggers match."""
    logging.config.dictConfig(LOG_CONFIG)


__all__ = ["LOG_CONFIG", "JsonFormatter", "configure_logging"]
