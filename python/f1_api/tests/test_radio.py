"""Phase 4.4: the team-radio corpus exposed through `f1_api`.

Unlike historical sessions, the radio corpus has no DB-free real backing at
all (`f1_api.radio`'s own module docstring) — so these tests use
`app.dependency_overrides` to swap in a `MemorySearchIndex`/
`MemoryTranscriptStore`/fake embedding backend, the same lever `main.py`
already uses for `get_settings`. The checked-in kernel fixture
(`api_fixture.SESSION_KEY`) carries no `team_radio_clip` events at all, so
`/sessions/{key}/radio` is asserted to degrade to `[]` rather than tested
against real per-session clip data — the streaming/pacing side of Phase 4.4
(a `team_radio_clip` event enriched into a `radio_clip` envelope) is covered
instead in `test_replay.py`, against a synthetic `MemorySource` session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from api_fixture import HISTORICAL_ROOT, SESSION_KEY
from fastapi.testclient import TestClient

from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.radio import (
    clip_id_path,
    get_embedding_backend_factory,
    get_radio_search_index,
    get_radio_store,
    parse_clip_id,
)
from f1_domain import SessionKey, SessionType
from f1_radio.search import MemorySearchIndex
from f1_radio.store import MemoryTranscriptStore, TranscriptRecord

KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_T0 = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


@dataclass
class _FakeEmbeddingBackend:
    """Mirrors `f1_radio/tests/radio_data.py`'s own `FakeEmbeddingBackend` —
    not imported directly since it lives in a sibling package's test tree,
    not on this package's import path."""

    vectors: dict[str, list[float]] = field(default_factory=dict)
    default: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text, self.default) for text in texts]


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


@pytest.fixture
def settings() -> Settings:
    return Settings(historical_root=HISTORICAL_ROOT)


def _client(
    settings: Settings,
    *,
    index: MemorySearchIndex | None = None,
    store: MemoryTranscriptStore | None = None,
    embedding_backend: _FakeEmbeddingBackend | None = None,
) -> TestClient:
    app = create_app(settings)
    if index is not None:
        app.dependency_overrides[get_radio_search_index] = lambda: index
    if store is not None:
        app.dependency_overrides[get_radio_store] = lambda: store
    backend = embedding_backend if embedding_backend is not None else _FakeEmbeddingBackend()
    app.dependency_overrides[get_embedding_backend_factory] = lambda: lambda: backend
    return TestClient(app)


# -- /radio/search ------------------------------------------------------------


def test_search_ranks_a_paraphrase_with_no_lexical_overlap_first(settings: Settings) -> None:
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
    backend = _FakeEmbeddingBackend(vectors={query: [0.98, 0.02, 0.0, 0.0]})

    response = _client(settings, index=index, embedding_backend=backend).get(
        "/radio/search", params={"q": query}
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    assert results[0]["clip"]["transcript_text"] == "front tyres are completely gone, box box"
    assert results[0]["similarity"] > results[1]["similarity"]
    assert results[0]["clip"]["lap_number"] == 20
    assert results[0]["clip"]["track_status"] == "green"
    assert results[0]["clip"]["has_transcript"] is True
    # Search results never carry an audio_url -- see RadioSearchResultOut's
    # own docstring for why (resolving it would mean re-reading every
    # matched session's event stream just to answer a ranked list).
    assert results[0]["clip"]["audio_url"] is None


def test_search_filters_exclude_a_candidate_even_when_its_embedding_matches_best(
    settings: Settings,
) -> None:
    matching_driver = _record(driver="44", audio_path="a.mp3", embedding=[1.0, 0.0, 0.0, 0.0])
    other_driver = _record(driver="1", audio_path="b.mp3", embedding=[1.0, 0.0, 0.0, 0.0])
    index = MemorySearchIndex([matching_driver, other_driver])
    query = "front tyres are gone"
    backend = _FakeEmbeddingBackend(vectors={query: [1.0, 0.0, 0.0, 0.0]})

    response = _client(settings, index=index, embedding_backend=backend).get(
        "/radio/search", params={"q": query, "driver": "44"}
    )

    results = response.json()
    assert len(results) == 1
    assert results[0]["clip"]["driver"] == "44"


def test_search_session_filter_resolves_season_round_and_type(settings: Settings) -> None:
    r1 = _record(session_key=KEY, audio_path="a.mp3")
    r2 = _record(
        session_key=SessionKey(year=2024, round=2, session_type=SessionType.RACE),
        audio_path="b.mp3",
    )
    index = MemorySearchIndex([r1, r2])

    response = _client(settings, index=index).get(
        "/radio/search", params={"q": "", "session": "2024-1-race"}
    )

    results = response.json()
    assert len(results) == 1
    assert results[0]["clip"]["session_key"] == {
        "year": 2024,
        "round": 1,
        "session_type": "race",
    }


def test_blank_query_runs_a_pure_filter_search_with_no_embedding_call(settings: Settings) -> None:
    index = MemorySearchIndex([_record(audio_path="a.mp3")])
    backend = _FakeEmbeddingBackend()

    response = _client(settings, index=index, embedding_backend=backend).get(
        "/radio/search", params={"q": ""}
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["similarity"] == 0.0
    assert backend.calls == []


def test_search_degrades_to_empty_list_when_corpus_unavailable(settings: Settings) -> None:
    """No `F1_STORE_DSN` and no override — `get_radio_search_index` degrades
    to `None`, the same "DB is a sink, never a dependency" rule
    `f1_api.sessions` already applies to historical/live sessions."""
    client = TestClient(create_app(settings))

    response = client.get("/radio/search", params={"q": "box box"})

    assert response.status_code == 200
    assert response.json() == []


# -- /sessions/{key}/radio ------------------------------------------------------


def test_session_radio_degrades_to_empty_list_when_the_fixture_has_no_clips(
    settings: Settings,
) -> None:
    """The checked-in kernel fixture ships no `team_radio_clip` events at
    all — `[]`, not an error, matching every other "nothing to report"
    state in this project."""
    client = TestClient(create_app(settings))

    response = client.get(f"/sessions/{SESSION_KEY}/radio")

    assert response.status_code == 200
    assert response.json() == []


def test_session_radio_404s_for_an_unknown_session(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/sessions/2099-1-race/radio")

    assert response.status_code == 404


# -- /radio/{clip_id} -----------------------------------------------------------


def test_radio_clip_by_id_returns_the_transcript_with_null_audio_url(settings: Settings) -> None:
    """The fixture session resolves (it exists in the historical backfill)
    but has no matching `TeamRadioClip` event, so `audio_url` degrades to
    `None` while the transcript itself is still returned in full — this
    project's "nothing silently dropped" discipline."""
    record = _record(session_key=KEY, driver="44", captured_at=_T0)
    store = MemoryTranscriptStore()
    store.insert(record)
    clip_id = clip_id_path(record.session_key, record.driver, record.captured_at)

    response = _client(settings, store=store).get(f"/radio/{clip_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["transcript_text"] == record.transcript_text
    assert body["has_transcript"] is True
    assert body["audio_url"] is None


def test_radio_clip_by_id_404s_when_no_transcript_exists(settings: Settings) -> None:
    store = MemoryTranscriptStore()
    clip_id = clip_id_path(KEY, "44", _T0)

    response = _client(settings, store=store).get(f"/radio/{clip_id}")

    assert response.status_code == 404


def test_radio_clip_by_id_503s_when_radio_corpus_unavailable(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    clip_id = clip_id_path(KEY, "44", _T0)

    response = client.get(f"/radio/{clip_id}")

    assert response.status_code == 503


def test_radio_clip_by_id_422s_on_a_malformed_clip_id(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/radio/not-a-real-clip-id")

    assert response.status_code == 422


# -- clip_id round-trip ---------------------------------------------------------


def test_clip_id_round_trips_through_parse_clip_id() -> None:
    clip_id = clip_id_path(KEY, "44", _T0)

    parsed_key, driver, captured_at = parse_clip_id(clip_id)

    assert parsed_key == KEY
    assert driver == "44"
    assert captured_at == _T0


def test_clip_id_round_trips_with_none_driver_and_captured_at() -> None:
    clip_id = clip_id_path(KEY, None, None)

    parsed_key, driver, captured_at = parse_clip_id(clip_id)

    assert parsed_key == KEY
    assert driver is None
    assert captured_at is None
