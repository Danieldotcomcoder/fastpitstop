"""The catalog: a manifest.json listing every session f1_backfill has
acquired, its source(s), per-table row counts, and when it was pulled.

This is what makes the backfill idempotent and resumable: `has()` is
checked before doing any network work for a session, and `--refresh` is
the only way to force a re-pull. It is deliberately a flat, DuckDB/pandas-
queryable JSON manifest rather than a database — Phase 0 has no store yet
(see CLAUDE.md: TimescaleDB doesn't arrive until Phase 1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from f1_backfill.schedule import SessionKey

CATALOG_FORMAT_VERSION = 1
CATALOG_FILENAME = "manifest.json"


class SessionEntry(BaseModel):
    """One acquired session's bookkeeping, keyed by `SessionKey.catalog_key`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    season: int
    round: int
    session_type: str
    event_name: str
    sources: list[str]
    tables: dict[str, int] = Field(description="table name -> row count")
    acquired_at: datetime


class Catalog(BaseModel):
    """In-memory view of the manifest. Load once, mutate via `record()`,
    `save()` explicitly when you want the change durable.
    """

    model_config = ConfigDict(extra="forbid")

    format_version: int = CATALOG_FORMAT_VERSION
    sessions: dict[str, SessionEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Catalog:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def has(self, key: SessionKey) -> bool:
        return key.catalog_key in self.sessions

    def record(
        self, key: SessionKey, *, sources: list[str], tables: dict[str, int], acquired_at: datetime
    ) -> None:
        self.sessions[key.catalog_key] = SessionEntry(
            season=key.season,
            round=key.round,
            session_type=key.session_type,
            event_name=key.event_name,
            sources=sources,
            tables=tables,
            acquired_at=acquired_at,
        )

    def record_table(self, key: SessionKey, table: str, rows: int) -> None:
        """Add (or replace) one table's row count on an existing entry — a
        table exported after the session itself was acquired."""
        entry = self.sessions[key.catalog_key]
        self.sessions[key.catalog_key] = entry.model_copy(
            update={"tables": {**entry.tables, table: rows}}
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
