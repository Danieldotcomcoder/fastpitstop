"""`fetch_clips`: idempotency/resumability, rate-limiting, backoff, and
graceful handling of missing/dead audio URLs -- the acquisition contract
this package exists for."""

from __future__ import annotations

import contextlib
from pathlib import Path

from radio_data import FakeClock, FakeResponse, FakeSession, RecordingRateLimiter, make_clip

from f1_radio.fetcher import fetch_clips
from f1_radio.manifest import ClipStatus, FetchManifest, clip_key


def _limiter(clock: FakeClock) -> RecordingRateLimiter:
    return RecordingRateLimiter(clock=clock, sleep=clock.sleep)


def test_fetch_downloads_and_writes_a_manifest_entry(tmp_path: Path) -> None:
    clip = make_clip()
    session = FakeSession([FakeResponse(200, content=b"audio-bytes")])
    clock = FakeClock()
    cache_root = tmp_path / "cache"
    manifest_path = tmp_path / "manifest.json"

    report = fetch_clips(
        [clip],
        cache_root=cache_root,
        manifest_path=manifest_path,
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.fetched == [key]
    assert report.skipped == report.no_url == report.failed == []

    manifest = FetchManifest.load(manifest_path)
    entry = manifest.clips[key]
    assert entry.status == ClipStatus.FETCHED
    assert entry.audio_path is not None
    audio_path = cache_root / entry.audio_path
    assert audio_path.read_bytes() == b"audio-bytes"


def test_second_run_is_idempotent_and_downloads_nothing_new(tmp_path: Path) -> None:
    clip = make_clip()
    cache_root = tmp_path / "cache"
    manifest_path = tmp_path / "manifest.json"
    clock = FakeClock()

    first_session = FakeSession([FakeResponse(200, content=b"audio-bytes")])
    fetch_clips(
        [clip],
        cache_root=cache_root,
        manifest_path=manifest_path,
        session=first_session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )
    assert len(first_session.calls) == 1

    # A second run gets a session with NO scripted responses at all -- if it
    # tried to re-download, this would raise IndexError (list.pop on empty).
    second_session = FakeSession([])
    report = fetch_clips(
        [clip],
        cache_root=cache_root,
        manifest_path=manifest_path,
        session=second_session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.skipped == [key]
    assert report.fetched == []
    assert second_session.calls == []


def test_refresh_forces_a_redownload(tmp_path: Path) -> None:
    clip = make_clip()
    cache_root = tmp_path / "cache"
    manifest_path = tmp_path / "manifest.json"
    clock = FakeClock()

    fetch_clips(
        [clip],
        cache_root=cache_root,
        manifest_path=manifest_path,
        session=FakeSession([FakeResponse(200, content=b"first")]),
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )

    second_session = FakeSession([FakeResponse(200, content=b"second")])
    report = fetch_clips(
        [clip],
        cache_root=cache_root,
        manifest_path=manifest_path,
        session=second_session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
        refresh=True,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.fetched == [key]
    assert len(second_session.calls) == 1
    manifest = FetchManifest.load(manifest_path)
    entry_audio_path = manifest.clips[key].audio_path
    assert entry_audio_path is not None
    assert (cache_root / entry_audio_path).read_bytes() == b"second"


def test_missing_audio_url_is_recorded_without_any_network_call(tmp_path: Path) -> None:
    clip = make_clip(audio_url=None)
    session = FakeSession([])  # any .get() call would raise IndexError
    clock = FakeClock()

    report = fetch_clips(
        [clip],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.no_url == [key]
    assert session.calls == []


def test_dead_url_is_recorded_and_the_batch_continues(tmp_path: Path) -> None:
    dead = make_clip(driver="44", audio_url="https://example.com/dead.mp3")
    good = make_clip(driver="1", audio_url="https://example.com/good.mp3")
    # 404 is non-retryable -- one response is enough to exhaust it.
    session = FakeSession([FakeResponse(404), FakeResponse(200, content=b"good-bytes")])
    clock = FakeClock()

    report = fetch_clips(
        [dead, good],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
    )

    dead_key = clip_key(dead.session_key, dead.driver, dead.captured_at)
    good_key = clip_key(good.session_key, good.driver, good.captured_at)
    assert [key for key, _ in report.failed] == [dead_key]
    assert report.fetched == [good_key]

    manifest = FetchManifest.load(tmp_path / "manifest.json")
    assert manifest.clips[dead_key].status == ClipStatus.FAILED
    assert manifest.clips[dead_key].error is not None
    assert manifest.clips[dead_key].audio_path is None


def test_transient_5xx_retries_with_backoff_then_succeeds(tmp_path: Path) -> None:
    clip = make_clip()
    session = FakeSession(
        [FakeResponse(503), FakeResponse(503), FakeResponse(200, content=b"eventually")]
    )
    clock = FakeClock()

    report = fetch_clips(
        [clip],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
        base_backoff_s=1.0,
        max_backoff_s=10.0,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.fetched == [key]
    assert len(session.calls) == 3
    assert clock.sleeps == [1.0, 2.0]  # exponential backoff between the two 503s


def test_retries_exhausted_records_failure_not_an_exception(tmp_path: Path) -> None:
    clip = make_clip()
    session = FakeSession([FakeResponse(503)] * 6)  # more than max_retries
    clock = FakeClock()

    report = fetch_clips(
        [clip],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
        max_retries=3,
        base_backoff_s=0.01,
        max_backoff_s=0.01,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.fetched == []
    assert [k for k, _ in report.failed] == [key]


def test_retry_after_header_is_honored_over_exponential_backoff(tmp_path: Path) -> None:
    clip = make_clip()
    session = FakeSession(
        [
            FakeResponse(429, headers={"Retry-After": "5"}),
            FakeResponse(200, content=b"ok"),
        ]
    )
    clock = FakeClock()

    fetch_clips(
        [clip],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=_limiter(clock),
        sleep=clock.sleep,
        base_backoff_s=1.0,
    )

    assert clock.sleeps == [5.0]


def test_every_http_attempt_goes_through_the_rate_limiter(tmp_path: Path) -> None:
    clip = make_clip()
    session = FakeSession([FakeResponse(503), FakeResponse(200, content=b"ok")])
    clock = FakeClock()
    limiter = _limiter(clock)

    fetch_clips(
        [clip],
        cache_root=tmp_path / "cache",
        manifest_path=tmp_path / "manifest.json",
        session=session,
        rate_limiter=limiter,
        sleep=clock.sleep,
        base_backoff_s=0.01,
    )

    assert limiter.acquire_count == 2  # one per attempt, including the retry


def test_manifest_is_durable_after_each_clip_not_only_at_the_end(tmp_path: Path) -> None:
    """Simulates a crash mid-batch: only the first of two clips gets a
    scripted response, so the second raises. The manifest on disk must still
    show the first clip as fetched -- each clip is a self-contained unit of
    work, matching f1_backfill's own per-session durability."""
    first = make_clip(driver="44")
    second = make_clip(driver="1")
    session = FakeSession([FakeResponse(200, content=b"first-bytes")])
    clock = FakeClock()
    manifest_path = tmp_path / "manifest.json"

    # The second clip's .get() call has no scripted response left, so this
    # raises IndexError partway through the batch -- suppressed here since
    # that's the point: it's simulating an interruption, not a real failure.
    with contextlib.suppress(IndexError):
        fetch_clips(
            [first, second],
            cache_root=tmp_path / "cache",
            manifest_path=manifest_path,
            session=session,
            rate_limiter=_limiter(clock),
            sleep=clock.sleep,
        )

    manifest = FetchManifest.load(manifest_path)
    first_key = clip_key(first.session_key, first.driver, first.captured_at)
    assert manifest.clips[first_key].status == ClipStatus.FETCHED
