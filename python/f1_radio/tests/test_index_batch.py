"""`run_index_batch`: idempotency (a clip already carrying an embedding from
the current model version is skipped, and the backend is never called
again), resumability considerations shared with `run_batch`, and the
no-transcript/empty-text/failed-batch edge cases -- all against
`MemoryTranscriptStore` and a `FakeEmbeddingBackend`, no database, no real
model."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from radio_data import KEY, FakeEmbeddingBackend, make_clip

from f1_domain import TrackStatusKind
from f1_radio.context import ClipContext
from f1_radio.index_batch import run_index_batch
from f1_radio.store import MemoryTranscriptStore, TranscriptRecord

_CAPTURED_AT = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


class _RaisingBackend:
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("boom")


def _seed_store(store: MemoryTranscriptStore, **overrides: object) -> None:
    fields: dict[str, object] = {
        "session_key": KEY,
        "driver": "44",
        "captured_at": _CAPTURED_AT,
        "audio_path": "a.mp3",
        "transcript_text": "front tyres are gone",
        "language": "en",
        "confidence": 0.9,
        "model_version": "faster-whisper-small",
        "segments": None,
    }
    fields.update(overrides)
    store.insert(TranscriptRecord(**fields))  # type: ignore[arg-type]


def test_a_transcribed_clip_gets_embedded_and_context_backfilled() -> None:
    store = MemoryTranscriptStore()
    _seed_store(store)
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=12, position=3, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )
    backend = FakeEmbeddingBackend(vectors={"front tyres are gone": [1.0, 0.0, 0.0, 0.0]})

    report = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="fake-model-v1",
    )

    assert len(report.indexed) == 1
    assert report.skipped == []
    assert report.no_transcript == []
    assert report.empty_text == []
    assert report.failed == []

    updated = store.get(KEY, "44", _CAPTURED_AT)
    assert updated is not None
    assert updated.embedding == [1.0, 0.0, 0.0, 0.0]
    assert updated.embedding_model_version == "fake-model-v1"
    assert updated.lap_number == 12
    assert updated.position == 3
    assert updated.track_status == "green"


def test_reindexing_with_the_same_model_version_is_a_no_op() -> None:
    store = MemoryTranscriptStore()
    _seed_store(store)
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=1, position=1, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )
    backend = FakeEmbeddingBackend(vectors={"front tyres are gone": [1.0, 0.0, 0.0, 0.0]})

    first = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="fake-model-v1",
    )
    assert len(first.indexed) == 1
    calls_after_first = len(backend.calls)

    second = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="fake-model-v1",
    )

    assert second.indexed == []
    assert len(second.skipped) == 1
    assert len(backend.calls) == calls_after_first  # the backend was never called again

    unchanged = store.get(KEY, "44", _CAPTURED_AT)
    assert unchanged is not None
    assert unchanged.embedding == [1.0, 0.0, 0.0, 0.0]


def test_a_different_model_version_re_embeds_rather_than_skipping() -> None:
    store = MemoryTranscriptStore()
    _seed_store(store)
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=1, position=1, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )
    backend = FakeEmbeddingBackend(vectors={"front tyres are gone": [1.0, 0.0, 0.0, 0.0]})
    run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="model-v1",
    )

    second_backend = FakeEmbeddingBackend(vectors={"front tyres are gone": [0.0, 1.0, 0.0, 0.0]})
    report = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: second_backend,
        model_version="model-v2",
    )

    assert len(report.indexed) == 1
    updated = store.get(KEY, "44", _CAPTURED_AT)
    assert updated is not None
    assert updated.embedding == [0.0, 1.0, 0.0, 0.0]
    assert updated.embedding_model_version == "model-v2"


def test_a_clip_with_no_transcript_row_yet_is_reported_not_indexed() -> None:
    store = MemoryTranscriptStore()  # nothing seeded
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=1, position=1, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )
    backend = FakeEmbeddingBackend()

    report = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="fake-model-v1",
    )

    assert report.indexed == []
    assert len(report.no_transcript) == 1
    assert backend.calls == []  # never loaded/called for a clip with nothing to embed


def test_a_transcript_with_no_text_is_reported_empty_not_indexed() -> None:
    store = MemoryTranscriptStore()
    _seed_store(store, transcript_text="", confidence=0.0)
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=1, position=1, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )
    backend = FakeEmbeddingBackend()

    report = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: backend,
        model_version="fake-model-v1",
    )

    assert report.indexed == []
    assert len(report.empty_text) == 1
    assert backend.calls == []


def test_a_backend_failure_is_reported_per_clip_not_a_crash() -> None:
    store = MemoryTranscriptStore()
    _seed_store(store)
    clip = make_clip(driver="44", captured_at=_CAPTURED_AT)
    ctx = ClipContext(
        lap_number=1, position=1, track_status=TrackStatusKind.GREEN, track_status_flags=[]
    )

    report = run_index_batch(
        [(clip, ctx)],
        store_factory=lambda: store,
        embedding_backend_factory=lambda: _RaisingBackend(),
        model_version="fake-model-v1",
    )

    assert report.indexed == []
    assert len(report.failed) == 1
    assert "boom" in report.failed[0][1]
    # the row is untouched -- no partial/garbage embedding written
    assert store.get(KEY, "44", _CAPTURED_AT) is not None
    assert store.get(KEY, "44", _CAPTURED_AT).embedding is None  # type: ignore[union-attr]


def test_no_pending_clips_never_loads_the_embedding_backend() -> None:
    store = MemoryTranscriptStore()  # nothing seeded, nothing to index
    calls: list[str] = []

    def _factory() -> FakeEmbeddingBackend:
        calls.append("loaded")
        return FakeEmbeddingBackend()

    report = run_index_batch(
        [],
        store_factory=lambda: store,
        embedding_backend_factory=_factory,
        model_version="fake-model-v1",
    )

    assert report.indexed == []
    assert calls == []
