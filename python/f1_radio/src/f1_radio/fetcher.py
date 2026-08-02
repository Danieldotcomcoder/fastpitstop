"""Downloads the audio referenced by `TeamRadioClip` events into a local,
deterministically-keyed cache (`f1_radio.cache`).

Resumable and idempotent: `fetch_clips` consults `FetchManifest` before doing
any network work and never re-downloads a clip already recorded there
(`refresh=True` forces a re-pull). Every request goes through a
`RateLimiter` (default: OpenF1's own free-tier budget -- team-radio audio is
reached via the same OpenF1-sourced URLs this project already has to be
polite about, per CLAUDE.md) and retries transient failures with backoff,
honoring `Retry-After` when the server sends one. A missing (`audio_url is
None`) or genuinely dead URL is recorded in the manifest and the batch moves
on -- it never raises out of `fetch_clips`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import requests

from f1_backfill.rate_limiter import RateLimiter
from f1_domain import TeamRadioClip
from f1_radio.cache import DEFAULT_CACHE_ROOT, clip_cache_path
from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_BACKOFF_S = 1.0
DEFAULT_MAX_BACKOFF_S = 60.0
DEFAULT_TIMEOUT_S = 30.0


class DownloadError(RuntimeError):
    """A clip's audio could not be fetched after retries were exhausted (or
    a non-retryable status was returned). Caught per-clip by `fetch_clips`
    so one dead URL never aborts the batch."""


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def raise_for_status(self) -> None: ...


class HttpSession(Protocol):
    def get(self, url: str, *, timeout: float | None = None) -> HttpResponse: ...


@dataclass
class FetchReport:
    fetched: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    no_url: list[str] = field(default_factory=list[str])
    failed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


def fetch_clips(
    events: Iterable[TeamRadioClip],
    *,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    manifest_path: Path,
    session: HttpSession | None = None,
    rate_limiter: RateLimiter | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_backoff_s: float = DEFAULT_BASE_BACKOFF_S,
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    refresh: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FetchReport:
    """Fetch every clip's audio not already recorded in the manifest at
    `manifest_path`, writing into `cache_root`. The manifest is saved after
    EVERY clip, not just at the end, so an interrupted run leaves
    already-fetched clips durably recorded and a re-run picks up where it
    left off.
    """
    http: HttpSession = session if session is not None else cast(HttpSession, requests.Session())
    limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    manifest = FetchManifest.load(manifest_path)
    report = FetchReport()

    for event in events:
        key = clip_key(event.session_key, event.driver, event.captured_at)
        if not refresh and manifest.has(key):
            report.skipped.append(key)
            continue

        if not event.audio_url:
            _record(
                manifest,
                manifest_path,
                key,
                event,
                status=ClipStatus.NO_URL,
                audio_path=None,
                now=now,
            )
            report.no_url.append(key)
            continue

        destination = clip_cache_path(
            cache_root, event.session_key, event.driver, event.captured_at, event.audio_url
        )
        try:
            content = _download(
                http,
                event.audio_url,
                limiter=limiter,
                max_retries=max_retries,
                base_backoff_s=base_backoff_s,
                max_backoff_s=max_backoff_s,
                timeout_s=timeout_s,
                sleep=sleep,
            )
        except DownloadError as exc:
            logger.warning("Failed to fetch clip %s: %s", key, exc)
            _record(
                manifest,
                manifest_path,
                key,
                event,
                status=ClipStatus.FAILED,
                audio_path=None,
                error=str(exc),
                now=now,
            )
            report.failed.append((key, str(exc)))
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        _record(
            manifest,
            manifest_path,
            key,
            event,
            status=ClipStatus.FETCHED,
            audio_path=str(destination.relative_to(cache_root)),
            now=now,
        )
        report.fetched.append(key)

    return report


def _record(
    manifest: FetchManifest,
    manifest_path: Path,
    key: str,
    event: TeamRadioClip,
    *,
    status: ClipStatus,
    audio_path: str | None,
    now: Callable[[], datetime],
    error: str | None = None,
) -> None:
    manifest.record(
        key,
        ClipEntry(
            year=event.session_key.year,
            round=event.session_key.round,
            session_type=event.session_key.session_type.value,
            driver=event.driver,
            captured_at=event.captured_at,
            audio_url=event.audio_url,
            audio_path=audio_path,
            status=status,
            error=error,
            fetched_at=now(),
        ),
    )
    manifest.save(manifest_path)


def _download(
    http: HttpSession,
    url: str,
    *,
    limiter: RateLimiter,
    max_retries: int,
    base_backoff_s: float,
    max_backoff_s: float,
    timeout_s: float,
    sleep: Callable[[float], None],
) -> bytes:
    attempt = 0
    while True:
        limiter.acquire()
        try:
            response = http.get(url, timeout=timeout_s)
        except requests.RequestException as exc:
            attempt += 1
            if attempt > max_retries:
                raise DownloadError(f"network error fetching {url}: {exc}") from exc
            sleep(min(base_backoff_s * (2 ** (attempt - 1)), max_backoff_s))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            attempt += 1
            if attempt > max_retries:
                raise DownloadError(
                    f"exceeded {max_retries} retries fetching {url} "
                    f"(last status {response.status_code})"
                )
            sleep(_backoff_seconds(response, attempt, base_backoff_s, max_backoff_s))
            continue

        if response.status_code == 404:
            raise DownloadError(f"audio not found (404) at {url}")

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DownloadError(f"HTTP {response.status_code} fetching {url}: {exc}") from exc
        return response.content


def _backoff_seconds(
    response: HttpResponse, attempt: int, base_backoff_s: float, max_backoff_s: float
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            logger.warning("Ignoring unparseable Retry-After header: %r", retry_after)
    return min(base_backoff_s * (2 ** (attempt - 1)), max_backoff_s)
