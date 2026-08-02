"""`embed_texts`/`embedding_model_version`: the thin wrapper around an
`EmbeddingBackend` -- all against a fake backend (no model, no
sentence-transformers/torch import needed for these).

`test_real_embeddings.py` is the opt-in sibling that runs a genuine semantic
claim (a paraphrase scores closer than an unrelated sentence) against a REAL
local model -- skipped by default, the same gated pattern
`test_real_model.py` already sets for faster-whisper.
"""

from __future__ import annotations

from radio_data import FakeEmbeddingBackend

from f1_radio.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts, embedding_model_version


def test_embedding_model_version_defaults_to_the_default_model_name() -> None:
    assert embedding_model_version() == DEFAULT_EMBEDDING_MODEL


def test_embedding_model_version_reflects_an_explicit_model_name() -> None:
    assert embedding_model_version("some/other-model") == "some/other-model"


def test_embed_texts_delegates_to_the_backend() -> None:
    backend = FakeEmbeddingBackend(vectors={"box box": [1.0, 0.0]})

    vectors = embed_texts(["box box"], backend)

    assert vectors == [[1.0, 0.0]]
    assert backend.calls == [["box box"]]


def test_embed_texts_of_an_empty_batch_never_calls_the_backend() -> None:
    backend = FakeEmbeddingBackend()

    vectors = embed_texts([], backend)

    assert vectors == []
    assert backend.calls == []
