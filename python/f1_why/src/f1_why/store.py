"""`PostgresExplanationStore`: explanations that survive a restart.

`MemoryExplanationStore` satisfies CLAUDE.md's "cache explanations per
anomaly; never re-generate" only for the life of one process. That was
enough while narration was effectively free. It is not now: the narrator
runs on a free tier capped at 50 requests a day, and every deploy emptied
the cache, so the same race's story was paid for again after each one.

The same Postgres/Memory split `f1_radio.store` makes for transcripts, and
the same honesty about failure: the store is an optimisation, never a
dependency. A database that is down or not yet migrated degrades to the
in-memory cache in front of it for a cooldown, logged once per outage, and
the race keeps streaming; persistence resumes by itself afterwards.

A connection per database round trip rather than one held open: psycopg
connections are not safe to share between threads, `f1_api` narrates a
session's moments on a small thread pool, and there are a dozen writes per
session at most. The in-memory layer in front means a warm read never
reaches the database at all.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from f1_why.narration import Explanation, ExplanationConfidence

_log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5
"""A dead database must cost a narration thread seconds, not a TCP timeout."""

NARRATION_VERSION = "2026-09-23"
"""Names the prompt and evidence rules an explanation was written under.

Bump it whenever either changes in a way that should produce different
sentences: stored explanations are keyed by it, so a bump re-narrates every
moment on its next request instead of serving text written under old rules.
2026-09-23: reasoning off, id=/roster prompt, relevant race control only,
measured lap context counts as support."""


def explanation_to_json(explanation: Explanation) -> dict[str, Any]:
    """`explanation` as a plain JSON object, for storage."""
    return {
        "anomaly_id": explanation.anomaly_id,
        "text": explanation.text,
        "cited_evidence_ids": list(explanation.cited_evidence_ids),
        "confidence": explanation.confidence.value,
        "grounded": explanation.grounded,
        "fallback_reason": explanation.fallback_reason,
    }


def explanation_from_json(payload: dict[str, Any]) -> Explanation:
    """Inverse of `explanation_to_json`."""
    return Explanation(
        anomaly_id=str(payload["anomaly_id"]),
        text=str(payload["text"]),
        cited_evidence_ids=tuple(str(item) for item in payload["cited_evidence_ids"]),
        confidence=ExplanationConfidence(payload["confidence"]),
        grounded=bool(payload["grounded"]),
        fallback_reason=(
            None if payload.get("fallback_reason") is None else str(payload["fallback_reason"])
        ),
    )


class PostgresExplanationStore:
    """An `ExplanationStore` that persists to the `explanations` table
    (migration 005), with an in-memory layer in front."""

    RETRY_AFTER_S = 300.0
    """How long to stay memory-only after the database fails. Long enough
    that a dead database is not retried once per explanation, short enough
    that a blip at startup does not cost persistence for the whole day."""

    def __init__(self, dsn: str, *, version: str = NARRATION_VERSION) -> None:
        self._dsn = dsn
        self._version = version
        self._memory: dict[str, Explanation] = {}
        self._lock = threading.Lock()
        self._degraded_until = 0.0

    @property
    def _degraded(self) -> bool:
        return time.monotonic() < self._degraded_until

    def get(self, anomaly_id: str) -> Explanation | None:
        with self._lock:
            cached = self._memory.get(anomaly_id)
        if cached is not None or self._degraded:
            return cached
        try:
            with psycopg.connect(self._dsn, connect_timeout=CONNECT_TIMEOUT_S) as conn:
                row = conn.execute(
                    "SELECT explanation FROM explanations "
                    "WHERE anomaly_id = %s AND narration_version = %s",
                    (anomaly_id, self._version),
                ).fetchone()
        except Exception:
            self._degrade("read")
            return None
        if row is None:
            return None
        explanation = explanation_from_json(row[0])
        with self._lock:
            self._memory[anomaly_id] = explanation
        return explanation

    def set(self, anomaly_id: str, explanation: Explanation) -> None:
        with self._lock:
            self._memory[anomaly_id] = explanation
        if self._degraded:
            return
        try:
            with psycopg.connect(self._dsn, connect_timeout=CONNECT_TIMEOUT_S) as conn:
                conn.execute(
                    "INSERT INTO explanations (anomaly_id, narration_version, explanation) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (anomaly_id, self._version, Jsonb(explanation_to_json(explanation))),
                )
        except Exception:
            self._degrade("write")

    def _degrade(self, operation: str) -> None:
        """Fall back to memory for `RETRY_AFTER_S`, and say so once per
        outage: a missing table or a dead database must not become a log
        line per explanation, nor an exception inside a race stream."""
        if not self._degraded:
            _log.warning(
                "explanation store %s failed; caching in memory only for %.0f s",
                operation,
                self.RETRY_AFTER_S,
                exc_info=True,
            )
        self._degraded_until = time.monotonic() + self.RETRY_AFTER_S


__all__ = [
    "NARRATION_VERSION",
    "PostgresExplanationStore",
    "explanation_from_json",
    "explanation_to_json",
]
