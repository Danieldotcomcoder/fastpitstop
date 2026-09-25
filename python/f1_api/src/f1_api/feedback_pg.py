"""Optional Postgres sink for feedback — imported only when a DSN is set."""

from __future__ import annotations

import json
from datetime import datetime

from f1_api.feedback import FeedbackRecord
from f1_store import connect


class PostgresFeedbackStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        with connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO feedback_submissions (
                    created_at, ip_hash, route, what_doing, what_happened,
                    what_expected, reply_email, diagnostics, email_sent, email_error
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id, created_at
                """,
                (
                    record.created_at,
                    record.ip_hash,
                    record.route,
                    record.what_doing,
                    record.what_happened,
                    record.what_expected,
                    record.reply_email,
                    json.dumps(record.diagnostics),
                    record.email_sent,
                    record.email_error,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            assert row is not None
            return FeedbackRecord(
                id=int(row[0]),
                created_at=row[1],
                ip_hash=record.ip_hash,
                route=record.route,
                what_doing=record.what_doing,
                what_happened=record.what_happened,
                what_expected=record.what_expected,
                reply_email=record.reply_email,
                diagnostics=record.diagnostics,
                email_sent=record.email_sent,
                email_error=record.email_error,
            )

    def mark_email(self, record_id: int, *, sent: bool, error: str | None) -> None:
        with connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE feedback_submissions SET email_sent = %s, email_error = %s WHERE id = %s",
                (sent, error, record_id),
            )
            conn.commit()

    def count_since(self, *, since: datetime, ip_hash: str | None = None) -> int:
        with connect(self._dsn) as conn, conn.cursor() as cur:
            if ip_hash is None:
                cur.execute(
                    "SELECT COUNT(*) FROM feedback_submissions WHERE created_at >= %s",
                    (since,),
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM feedback_submissions "
                    "WHERE created_at >= %s AND ip_hash = %s",
                    (since, ip_hash),
                )
            row = cur.fetchone()
            return int(row[0]) if row else 0


__all__ = ["PostgresFeedbackStore"]
