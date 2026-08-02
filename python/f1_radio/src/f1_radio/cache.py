"""Deterministic on-disk cache path for a clip's audio.

`data/radio_audio/` by default, keyed by (session, driver, captured_at) --
never content-hashed, so a re-run always resolves to the SAME path for the
same clip without reading anything back first. `fetch_clips` (`fetcher.py`)
is what actually writes here; this module is pure path arithmetic so it's
testable with no filesystem I/O.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from f1_domain import SessionKey

DEFAULT_CACHE_ROOT = Path("data/radio_audio")

# F1 team-radio clips are served as mp3 in practice; used only when the URL
# itself carries no discoverable extension.
DEFAULT_AUDIO_SUFFIX = ".mp3"


def clip_cache_path(
    cache_root: Path,
    session_key: SessionKey,
    driver: str | None,
    captured_at: datetime | None,
    audio_url: str | None,
) -> Path:
    """The path this clip's audio is stored at (whether or not it has been
    fetched yet). One directory per session; `driver`/`captured_at` are the
    clip's own identity, so two runs against the same clip always agree on
    this path with no lookup required.
    """
    session_dir = (
        cache_root
        / str(session_key.year)
        / f"{session_key.round:02d}_{session_key.session_type.value}"
    )
    driver_part = driver or "unknown"
    ts_part = _timestamp_slug(captured_at)
    return session_dir / f"{driver_part}_{ts_part}{_suffix_from_url(audio_url)}"


def _timestamp_slug(captured_at: datetime | None) -> str:
    if captured_at is None:
        return "unknown"
    return captured_at.strftime("%Y%m%dT%H%M%S_%fZ")


def _suffix_from_url(audio_url: str | None) -> str:
    if not audio_url:
        return DEFAULT_AUDIO_SUFFIX
    suffix = Path(urlparse(audio_url).path).suffix
    return suffix if suffix else DEFAULT_AUDIO_SUFFIX
