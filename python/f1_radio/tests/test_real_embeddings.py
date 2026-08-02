"""Opt-in: `load_embedding_model`/`embed_texts` against a REAL local
sentence-transformers model -- the genuine semantic claim
`test_search.py`'s `FakeEmbeddingBackend`-based tests cannot make on their
own (a fake's vectors are whatever the test plants; only a real model
proves an ACTUAL paraphrase embeds close to its source text with no lexical
overlap).

Gated (skipped by default): downloads a real model from the Hugging Face
Hub on first run and needs real CPU time, the same "not exercisable in
every environment" reality `test_real_model.py` already accepts for
faster-whisper. Opt in with:

    F1_RADIO_RUN_MODEL_TESTS=1 uv run pytest python/f1_radio/tests/test_real_embeddings.py
"""

from __future__ import annotations

import math
import os

import pytest

from f1_radio.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts, load_embedding_model

pytestmark = pytest.mark.skipif(
    os.environ.get("F1_RADIO_RUN_MODEL_TESTS") != "1",
    reason="model-gated: set F1_RADIO_RUN_MODEL_TESTS=1 to download+run a real model",
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


@pytest.fixture(scope="module")
def backend():
    return load_embedding_model(DEFAULT_EMBEDDING_MODEL)


def test_a_paraphrase_embeds_closer_than_an_unrelated_sentence(backend: object) -> None:
    source = "My front tyres are completely gone, I have no grip at all."
    paraphrase = "There's nothing left on the front axle, box box."
    unrelated = "What is the gap to the car behind me right now?"

    [source_vec, paraphrase_vec, unrelated_vec] = embed_texts(
        [source, paraphrase, unrelated],
        backend,  # type: ignore[arg-type]
    )

    close = _cosine(source_vec, paraphrase_vec)
    far = _cosine(source_vec, unrelated_vec)
    assert close > far


def test_vectors_are_unit_norm() -> None:
    backend = load_embedding_model(DEFAULT_EMBEDDING_MODEL)
    [vector] = embed_texts(["box this lap"], backend)

    norm = math.sqrt(sum(x * x for x in vector))
    assert norm == pytest.approx(1.0, abs=1e-3)
