"""`search_transcripts`/`MemorySearchIndex`: semantic ranking by cosine
similarity, structured filters composing correctly with a semantic query,
and the fact that filters exclude a candidate even when its embedding is
the closest match -- all in-memory, no database, no real model. The
paraphrase claim itself ("finds a relevant clip with none of the same
words") is proven at the unit level here via a `FakeEmbeddingBackend` that
the test controls directly (mirroring `FakeWhisperBackend`'s own
precedent) -- the REAL semantic claim, that an actual model embeds a
paraphrase close to its source text, is `test_real_embeddings.py`'s job
(opt-in, a real model)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radio_data import FakeEmbeddingBackend

from f1_domain import SessionKey, SessionType
from f1_radio.search import MemorySearchIndex, SearchFilters, search_transcripts
from f1_radio.store import TranscriptRecord

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
KEY_R2 = SessionKey(year=2024, round=2, session_type=SessionType.RACE)

_T0 = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def _record(**overrides: object) -> TranscriptRecord:
    fields: dict[str, object] = {
        "session_key": KEY,
        "driver": "44",
        "captured_at": _T0,
        "audio_path": "a.mp3",
        "transcript_text": "front tyres are completely gone, box box",
        "language": "en",
        "confidence": 0.9,
        "model_version": "faster-whisper-small",
        "segments": None,
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "embedding_model_version": "fake-model-v1",
        "lap_number": 20,
        "position": 3,
        "track_status": "green",
        "track_status_flags": [],
    }
    fields.update(overrides)
    return TranscriptRecord(**fields)  # type: ignore[arg-type]


def test_paraphrased_query_ranks_the_semantically_close_clip_first() -> None:
    """The corpus has no lexical overlap between the query and the matching
    transcript -- only the planted embedding closeness connects them."""
    front_tyres = _record(
        audio_path="a.mp3",
        transcript_text="front tyres are completely gone, box box",
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    unrelated = _record(
        audio_path="b.mp3",
        transcript_text="gap to the car ahead is one point two",
        embedding=[0.0, 0.0, 1.0, 0.0],
    )
    index = MemorySearchIndex([unrelated, front_tyres])
    query = "there's nothing left at the front, box this lap"
    backend = FakeEmbeddingBackend(vectors={query: [0.98, 0.02, 0.0, 0.0]})

    results = search_transcripts(query, index=index, embedding_backend=backend)

    assert results[0].record.audio_path == "a.mp3"
    assert results[0].similarity > results[1].similarity
    assert results[0].similarity > 0.9


def test_filters_exclude_a_candidate_even_when_its_embedding_matches_best() -> None:
    matching_driver = _record(driver="44", audio_path="a.mp3", embedding=[1.0, 0.0, 0.0, 0.0])
    other_driver_same_vector = _record(
        driver="1", audio_path="b.mp3", embedding=[1.0, 0.0, 0.0, 0.0]
    )
    other_round_same_vector = _record(
        driver="44", session_key=KEY_R2, audio_path="c.mp3", embedding=[1.0, 0.0, 0.0, 0.0]
    )
    index = MemorySearchIndex([matching_driver, other_driver_same_vector, other_round_same_vector])
    query = "front tyres are gone"
    backend = FakeEmbeddingBackend(vectors={query: [1.0, 0.0, 0.0, 0.0]})

    results = search_transcripts(
        query,
        index=index,
        embedding_backend=backend,
        filters=SearchFilters(driver="44", season=2024, round=1),
    )

    assert len(results) == 1
    assert results[0].record.audio_path == "a.mp3"


def test_keyword_filter_composes_with_driver_and_semantic_query() -> None:
    matches_keyword = _record(
        driver="44", audio_path="a.mp3", transcript_text="box box, front tyres are gone"
    )
    no_keyword = _record(
        driver="44",
        audio_path="b.mp3",
        transcript_text="gap is stable",
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    index = MemorySearchIndex([matches_keyword, no_keyword])
    query = "front tyres are gone"
    backend = FakeEmbeddingBackend(vectors={query: [1.0, 0.0, 0.0, 0.0]})

    results = search_transcripts(
        query, index=index, embedding_backend=backend, filters=SearchFilters(keyword="front tyres")
    )

    assert len(results) == 1
    assert results[0].record.audio_path == "a.mp3"


def test_lap_and_track_status_filters_narrow_results() -> None:
    on_lap_20_green = _record(audio_path="a.mp3", lap_number=20, track_status="green")
    on_lap_5_sc = _record(audio_path="b.mp3", lap_number=5, track_status="sc")
    index = MemorySearchIndex([on_lap_20_green, on_lap_5_sc])

    by_lap = MemorySearchIndex([on_lap_20_green, on_lap_5_sc]).search(
        None, filters=SearchFilters(lap_number=5), limit=10
    )
    assert [r.record.audio_path for r in by_lap] == ["b.mp3"]

    by_status = index.search(None, filters=SearchFilters(track_status="sc"), limit=10)
    assert [r.record.audio_path for r in by_status] == ["b.mp3"]


def test_date_range_filter_excludes_clips_outside_the_window() -> None:
    early = _record(audio_path="a.mp3", captured_at=_T0)
    late = _record(audio_path="b.mp3", captured_at=_T0 + timedelta(hours=1))
    index = MemorySearchIndex([early, late])

    results = index.search(
        None,
        filters=SearchFilters(captured_after=_T0 + timedelta(minutes=30)),
        limit=10,
    )

    assert [r.record.audio_path for r in results] == ["b.mp3"]


def test_a_record_with_no_stored_embedding_never_matches_a_semantic_query() -> None:
    no_embedding = _record(audio_path="a.mp3", embedding=None)
    index = MemorySearchIndex([no_embedding])
    query = "front tyres are gone"
    backend = FakeEmbeddingBackend(vectors={query: [1.0, 0.0, 0.0, 0.0]})

    results = search_transcripts(query, index=index, embedding_backend=backend)

    assert results == []


def test_blank_query_runs_a_pure_filter_search_with_no_embedding_call() -> None:
    record = _record(audio_path="a.mp3")
    index = MemorySearchIndex([record])
    backend = FakeEmbeddingBackend()

    results = search_transcripts("", index=index, embedding_backend=backend)

    assert len(results) == 1
    assert results[0].similarity == 0.0
    assert backend.calls == []
