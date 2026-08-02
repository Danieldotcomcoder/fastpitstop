"""`run_batch`: idempotency, resumability, and safe parallel execution --
all against a fake `WhisperBackend` and `MemoryTranscriptStore`, no real
model and no database, mirroring this package's own fake-over-mock /
DB-optional testing discipline (`test_fetcher.py`, `f1_store.MemorySink`)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from radio_data import (
    FakeWhisperBackend,
    FakeWhisperInfo,
    FakeWhisperSegment,
    clean_segment,
    make_clip,
)

from f1_domain import TeamRadioClip
from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key
from f1_radio.store import MemoryTranscriptStore, TranscriptRecord
from f1_radio.transcribe_batch import run_batch


def _fetched_manifest(
    events: Iterable[TeamRadioClip], audio_paths: dict[str, str] | None = None
) -> FetchManifest:
    manifest = FetchManifest()
    for i, event in enumerate(events):
        key = clip_key(event.session_key, event.driver, event.captured_at)
        rel_path = (audio_paths or {}).get(key, f"clip_{i}.mp3")
        manifest.record(
            key,
            ClipEntry(
                year=event.session_key.year,
                round=event.session_key.round,
                session_type=event.session_key.session_type.value,
                driver=event.driver,
                captured_at=event.captured_at,
                audio_url=event.audio_url,
                audio_path=rel_path,
                status=ClipStatus.FETCHED,
                fetched_at=datetime(2024, 3, 2, 16, 0, tzinfo=UTC),
            ),
        )
    return manifest


def test_transcribes_a_fetched_clip_and_writes_a_transcript_record(tmp_path: Path) -> None:
    clip = make_clip(driver="44")
    manifest = _fetched_manifest([clip])
    store = MemoryTranscriptStore()
    backend = FakeWhisperBackend(segments=[clean_segment("Box this lap.", avg_logprob=-0.1)])

    report = run_batch(
        [clip],
        cache_root=tmp_path,
        manifest=manifest,
        store_factory=lambda: store,
        model_factory=lambda: backend,
        workers=1,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.transcribed == [key]
    assert report.skipped == report.no_audio == report.failed == []
    [record] = store.all()
    assert record.transcript_text == "Box this lap."
    assert record.model_version.startswith("faster-whisper-")
    assert record.segments is not None
    assert record.segments[0]["text"] == "Box this lap."


def test_a_clip_with_no_fetched_audio_is_skipped_as_no_audio(tmp_path: Path) -> None:
    clip = make_clip(driver="1")
    store = MemoryTranscriptStore()
    backend = FakeWhisperBackend()

    report = run_batch(
        [clip],
        cache_root=tmp_path,
        manifest=FetchManifest(),  # nothing fetched
        store_factory=lambda: store,
        model_factory=lambda: backend,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.no_audio == [key]
    assert report.transcribed == []
    assert backend.calls == []


def test_a_clip_that_already_has_a_transcript_is_skipped_idempotently(tmp_path: Path) -> None:
    clip = make_clip(driver="44")
    manifest = _fetched_manifest([clip])
    store = MemoryTranscriptStore()
    store.insert(
        TranscriptRecord(
            session_key=clip.session_key,
            driver=clip.driver,
            captured_at=clip.captured_at,
            audio_path="clip_0.mp3",
            transcript_text="already done",
            language="en",
            confidence=0.9,
            model_version="faster-whisper-small",
        )
    )
    backend = FakeWhisperBackend()

    report = run_batch(
        [clip],
        cache_root=tmp_path,
        manifest=manifest,
        store_factory=lambda: store,
        model_factory=lambda: backend,
    )

    key = clip_key(clip.session_key, clip.driver, clip.captured_at)
    assert report.skipped == [key]
    assert report.transcribed == []
    assert backend.calls == []  # no wasted model work on an already-done clip


class _FlakyBackend:
    """Fails on one specific clip -- simulates the process dying partway
    through a batch, so a second `run_batch` call can prove resumability."""

    def __init__(self, fail_on_substring: str) -> None:
        self.fail_on_substring = fail_on_substring
        self.calls: list[str] = []

    def transcribe(
        self, audio_path: str, /, *, vad_filter: bool
    ) -> tuple[list[FakeWhisperSegment], FakeWhisperInfo]:
        del vad_filter
        self.calls.append(audio_path)
        if self.fail_on_substring in audio_path:
            raise RuntimeError("simulated failure")
        return [clean_segment("Box this lap.", avg_logprob=-0.1)], FakeWhisperInfo()


def test_resumable_after_a_partial_run(tmp_path: Path) -> None:
    good = make_clip(driver="44")
    bad = make_clip(driver="1")
    manifest = _fetched_manifest([good, bad])
    store = MemoryTranscriptStore()

    first_backend = _FlakyBackend(fail_on_substring="clip_1")
    report1 = run_batch(
        [good, bad],
        cache_root=tmp_path,
        manifest=manifest,
        store_factory=lambda: store,
        model_factory=lambda: first_backend,
    )
    good_key = clip_key(good.session_key, good.driver, good.captured_at)
    bad_key = clip_key(bad.session_key, bad.driver, bad.captured_at)
    assert report1.transcribed == [good_key]
    assert [k for k, _ in report1.failed] == [bad_key]

    # Second run: `good` must not be re-transcribed; only `bad` is retried,
    # this time against a backend that succeeds.
    second_backend = FakeWhisperBackend(segments=[clean_segment("Copy.", avg_logprob=-0.1)])
    report2 = run_batch(
        [good, bad],
        cache_root=tmp_path,
        manifest=manifest,
        store_factory=lambda: store,
        model_factory=lambda: second_backend,
    )
    assert report2.skipped == [good_key]
    assert report2.transcribed == [bad_key]
    assert second_backend.calls == [str(tmp_path / "clip_1.mp3")]
    assert len(store.all()) == 2


def test_parallel_workers_never_double_process_or_double_write(tmp_path: Path) -> None:
    clips = [make_clip(driver=str(i)) for i in range(8)]
    manifest = _fetched_manifest(clips)
    store = MemoryTranscriptStore()
    model_loads: list[int] = []

    def model_factory() -> FakeWhisperBackend:
        model_loads.append(1)
        return FakeWhisperBackend(segments=[clean_segment("Box.", avg_logprob=-0.1)])

    report = run_batch(
        clips,
        cache_root=tmp_path,
        manifest=manifest,
        store_factory=lambda: store,
        model_factory=model_factory,
        workers=4,
    )

    assert len(report.transcribed) == 8
    assert report.failed == []
    all_records = store.all()
    assert len(all_records) == 8  # exactly one row per clip, no duplicates
    assert len({r.driver for r in all_records}) == 8
    # A model is loaded (and cached) once per worker THREAD, never once per clip.
    assert 1 <= len(model_loads) <= 4
