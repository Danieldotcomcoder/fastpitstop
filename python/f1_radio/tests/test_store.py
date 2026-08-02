"""`MemoryTranscriptStore.set_embedding` and the `vector_literal`/
`record_from_row` round trip -- no database needed for either."""

from __future__ import annotations

from datetime import UTC, datetime

from radio_data import KEY

from f1_radio.store import (
    ALL_COLUMNS,
    MemoryTranscriptStore,
    TranscriptRecord,
    record_from_row,
    vector_literal,
)

_CAPTURED_AT = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def _seeded_store() -> MemoryTranscriptStore:
    store = MemoryTranscriptStore()
    store.insert(
        TranscriptRecord(
            session_key=KEY,
            driver="44",
            captured_at=_CAPTURED_AT,
            audio_path="a.mp3",
            transcript_text="box this lap",
            language="en",
            confidence=0.9,
            model_version="faster-whisper-small",
        )
    )
    return store


def test_set_embedding_backfills_a_seeded_record() -> None:
    store = _seeded_store()

    updated = store.set_embedding(
        KEY,
        "44",
        _CAPTURED_AT,
        embedding=[0.1, 0.2, 0.3],
        embedding_model_version="fake-model-v1",
        lap_number=12,
        position=4,
        track_status="yellow",
        track_status_flags=["yellow"],
    )

    assert updated is True
    record = store.get(KEY, "44", _CAPTURED_AT)
    assert record is not None
    assert record.embedding == [0.1, 0.2, 0.3]
    assert record.embedding_model_version == "fake-model-v1"
    assert record.lap_number == 12
    assert record.position == 4
    assert record.track_status == "yellow"
    assert record.track_status_flags == ["yellow"]
    # the base fields this seeded record had are untouched
    assert record.transcript_text == "box this lap"


def test_set_embedding_on_an_unknown_clip_is_a_no_op() -> None:
    store = MemoryTranscriptStore()

    updated = store.set_embedding(
        KEY,
        "44",
        _CAPTURED_AT,
        embedding=[0.1, 0.2, 0.3],
        embedding_model_version="fake-model-v1",
        lap_number=1,
        position=1,
        track_status="green",
        track_status_flags=[],
    )

    assert updated is False


def _row(**overrides: object) -> tuple[object, ...]:
    """A synthetic `ALL_COLUMNS`-ordered row, the shape `record_from_row`
    (and, in real use, a `SELECT {ALL_COLUMNS} FROM radio_transcripts` cursor
    row) expects."""
    fields: dict[str, object] = {
        "session_year": KEY.year,
        "session_round": KEY.round,
        "session_type": KEY.session_type.value,
        "driver": "44",
        "captured_at": _CAPTURED_AT,
        "audio_path": "a.mp3",
        "transcript_text": "box this lap",
        "language": "en",
        "confidence": 0.9,
        "model_version": "faster-whisper-small",
        "segments": None,
        "embedding": None,
        "embedding_model_version": None,
        "lap_number": None,
        "position": None,
        "track_status": None,
        "track_status_flags": None,
    }
    fields.update(overrides)
    return tuple(fields[name] for name in ALL_COLUMNS)


def test_vector_literal_round_trips_through_record_from_row() -> None:
    """No custom psycopg type adapter is registered for pgvector's `vector`
    type (see `f1_radio.store`'s own module docstring) -- a real SELECTed
    column comes back as `vector_literal`'s own text format, which
    `record_from_row` must parse back into a plain float list."""
    values = [0.1, -0.25, 3.0]
    row = _row(embedding=vector_literal(values))

    record = record_from_row(row)

    assert record.embedding == values


def test_a_null_embedding_column_is_none_not_an_empty_list() -> None:
    row = _row(embedding=None)

    record = record_from_row(row)

    assert record.embedding is None
