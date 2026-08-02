"""The fetch manifest: what `f1_backfill.catalog.Catalog` is to historical
sessions, but for individual radio clips. `has()` gates network work in
`fetcher.fetch_clips`; `record()` + `save()` after every clip (not just at
the end) is what makes an interrupted fetch resumable rather than
all-or-nothing -- the same "each unit of work is self-contained" discipline
`f1_backfill.pipeline.run_backfill` uses for sessions.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from f1_domain import SessionKey

MANIFEST_FORMAT_VERSION = 1
MANIFEST_FILENAME = "manifest.json"


class ClipStatus(StrEnum):
    """The outcome recorded for one clip. `NO_URL` and `FAILED` are both
    non-fatal, expected outcomes -- see `fetcher.fetch_clips`."""

    FETCHED = "fetched"
    NO_URL = "no_url"
    FAILED = "failed"


def clip_key(session_key: SessionKey, driver: str | None, captured_at: datetime | None) -> str:
    """Deterministic identity for one clip: (session, driver, captured_at)
    -- exactly the key this package's cache and manifest share."""
    ts = captured_at.isoformat() if captured_at is not None else "unknown"
    return (
        f"{session_key.year}_{session_key.round:02d}_{session_key.session_type.value}"
        f"_{driver or 'unknown'}_{ts}"
    )


class ClipEntry(BaseModel):
    """One clip's bookkeeping, keyed by `clip_key(...)` in `FetchManifest.clips`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    round: int
    session_type: str
    driver: str | None
    captured_at: datetime | None
    audio_url: str | None
    audio_path: str | None = Field(
        default=None, description="Relative to the cache root; set only when status is FETCHED."
    )
    status: ClipStatus
    error: str | None = None
    fetched_at: datetime


class FetchManifest(BaseModel):
    """In-memory view of the manifest. Load once, mutate via `record()`,
    `save()` explicitly when you want the change durable."""

    model_config = ConfigDict(extra="forbid")

    format_version: int = MANIFEST_FORMAT_VERSION
    clips: dict[str, ClipEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> FetchManifest:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def has(self, key: str) -> bool:
        return key in self.clips

    def record(self, key: str, entry: ClipEntry) -> None:
        self.clips[key] = entry

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
