"""`radio_clip`: joins one `TeamRadioClip` event to its manifest entry (the
cached audio path, if fetched) and its transcript (via an injected lookup,
`None` by default -- transcription isn't built yet)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from radio_data import KEY, make_clip

from f1_domain import SessionKey, SessionType
from f1_radio.domain import radio_clip
from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key
from f1_radio.store import TranscriptRecord

_CACHE_ROOT = Path("cache")


def test_no_manifest_entry_means_no_audio_path() -> None:
    event = make_clip()
    clip = radio_clip(event, cache_root=_CACHE_ROOT, manifest=FetchManifest())

    assert clip.audio_path is None
    assert not clip.has_audio


def test_a_fetched_entry_resolves_the_audio_path_under_cache_root() -> None:
    event = make_clip()
    manifest = FetchManifest()
    key = clip_key(event.session_key, event.driver, event.captured_at)
    manifest.record(
        key,
        ClipEntry(
            year=event.session_key.year,
            round=event.session_key.round,
            session_type=event.session_key.session_type.value,
            driver=event.driver,
            captured_at=event.captured_at,
            audio_url=event.audio_url,
            audio_path="2024/01_race/44_x.mp3",
            status=ClipStatus.FETCHED,
            fetched_at=datetime(2024, 3, 2, 16, 0, tzinfo=UTC),
        ),
    )

    clip = radio_clip(event, cache_root=_CACHE_ROOT, manifest=manifest)

    assert clip.has_audio
    assert clip.audio_path == _CACHE_ROOT / "2024/01_race/44_x.mp3"


def test_a_failed_entry_resolves_no_audio_path() -> None:
    event = make_clip()
    manifest = FetchManifest()
    key = clip_key(event.session_key, event.driver, event.captured_at)
    manifest.record(
        key,
        ClipEntry(
            year=event.session_key.year,
            round=event.session_key.round,
            session_type=event.session_key.session_type.value,
            driver=event.driver,
            captured_at=event.captured_at,
            audio_url=event.audio_url,
            audio_path=None,
            status=ClipStatus.FAILED,
            error="dead url",
            fetched_at=datetime(2024, 3, 2, 16, 0, tzinfo=UTC),
        ),
    )

    clip = radio_clip(event, cache_root=_CACHE_ROOT, manifest=manifest)

    assert clip.audio_path is None


def test_default_transcript_lookup_returns_none() -> None:
    event = make_clip()
    clip = radio_clip(event, cache_root=_CACHE_ROOT, manifest=FetchManifest())

    assert clip.transcript is None
    assert not clip.has_transcript


def test_injected_transcript_lookup_is_used() -> None:
    event = make_clip()
    record = TranscriptRecord(
        session_key=KEY,
        driver=event.driver,
        captured_at=event.captured_at,
        audio_path="2024/01_race/44_x.mp3",
        transcript_text="box this lap",
        language="en",
        confidence=0.95,
        model_version="whisper-test",
    )

    def lookup(
        session_key: SessionKey, driver: str | None, captured_at: datetime | None
    ) -> TranscriptRecord | None:
        return record

    clip = radio_clip(
        event,
        cache_root=_CACHE_ROOT,
        manifest=FetchManifest(),
        transcript_lookup=lookup,
    )

    assert clip.has_transcript
    assert clip.transcript is record


def test_session_type_round_trips_through_the_clip() -> None:
    event = make_clip()
    clip = radio_clip(event, cache_root=_CACHE_ROOT, manifest=FetchManifest())
    assert clip.session_key.session_type == SessionType.RACE
