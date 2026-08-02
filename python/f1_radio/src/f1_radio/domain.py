"""`RadioClip`: the read-side view of one driver-pit exchange, joining the
originating `TeamRadioClip` event to its cached audio (`f1_radio.fetcher`'s
manifest) and its transcript (`f1_radio.store`, once Phase 4's transcription
step exists). This package builds only the join shape -- `transcript` is
always `None` here unless a caller supplies a real `transcript_lookup`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from f1_domain import SessionKey, TeamRadioClip
from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key
from f1_radio.store import TranscriptRecord

TranscriptLookup = Callable[[SessionKey, str | None, datetime | None], TranscriptRecord | None]


def _no_transcript(
    session_key: SessionKey, driver: str | None, captured_at: datetime | None
) -> TranscriptRecord | None:
    del session_key, driver, captured_at
    return None


@dataclass(frozen=True, slots=True)
class RadioClip:
    session_key: SessionKey
    driver: str | None
    captured_at: datetime | None
    audio_url: str | None
    audio_path: Path | None
    transcript: TranscriptRecord | None

    @property
    def has_audio(self) -> bool:
        return self.audio_path is not None

    @property
    def has_transcript(self) -> bool:
        return self.transcript is not None


def radio_clip(
    event: TeamRadioClip,
    *,
    cache_root: Path,
    manifest: FetchManifest,
    transcript_lookup: TranscriptLookup = _no_transcript,
) -> RadioClip:
    """Join one `TeamRadioClip` event to its fetch-manifest entry (the
    cached audio path, if fetched) and its transcript (via
    `transcript_lookup`, `None` by default until a caller wires up
    `f1_radio.store.get_transcript`).
    """
    key = clip_key(event.session_key, event.driver, event.captured_at)
    entry: ClipEntry | None = manifest.clips.get(key)
    audio_path = _resolve_audio_path(cache_root, entry)
    return RadioClip(
        session_key=event.session_key,
        driver=event.driver,
        captured_at=event.captured_at,
        audio_url=event.audio_url,
        audio_path=audio_path,
        transcript=transcript_lookup(event.session_key, event.driver, event.captured_at),
    )


def _resolve_audio_path(cache_root: Path, entry: ClipEntry | None) -> Path | None:
    if entry is None or entry.status != ClipStatus.FETCHED or entry.audio_path is None:
        return None
    return cache_root / entry.audio_path
