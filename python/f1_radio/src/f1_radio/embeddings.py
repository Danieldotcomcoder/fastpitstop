"""Local sentence embeddings over the transcript corpus (Phase 4.3) -- the
step that makes it a SEARCHABLE dataset, not just a pile of text. Runs
entirely local (`sentence-transformers`, CPU-friendly), no paid embedding
API, matching CLAUDE.md's Phase 4 budget invariant and the same
local-model precedent `f1_radio.transcription` already sets for
faster-whisper.

`EmbeddingBackend` is a narrow structural Protocol -- only the one call
this module makes -- so tests substitute a fake with no
`sentence-transformers`/`torch` import, mirroring `WhisperBackend`'s own
precedent. `load_embedding_model` imports the real library lazily for the
same reason `load_model` does: importing this module never requires the
model's heavy dependency chain to be installed, only actually loading one
does.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

EMBEDDING_DIM = 384
"""The output dimension of `DEFAULT_EMBEDDING_MODEL` -- must match the
`vector(384)` column width in
`f1_store/migrations/003_radio_embeddings.sql`. Switching to a model with a
different dimension needs a new migration, not just a config change."""


def embedding_model_version(model_name: str = DEFAULT_EMBEDDING_MODEL) -> str:
    """The string recorded in `radio_transcripts.embedding_model_version` --
    identifies which model produced a row's vector, the same way
    `f1_radio.transcription.model_version` identifies a transcript's model.
    A later re-embed with a different model is told apart from this one,
    which is what makes re-indexing idempotent (`f1_radio.index_batch`
    skips a clip whose stored version already matches)."""
    return model_name


class EmbeddingBackend(Protocol):
    """Structural match to `sentence_transformers.SentenceTransformer`'s
    own `.encode` -- narrowed to what this module calls, so a fake
    implementing just this shape is a legitimate test substitute."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class _SentenceTransformerBackend:
    """Wraps a real `SentenceTransformer` to the plain `list[list[float]]`
    shape `EmbeddingBackend` promises -- the real model's `.encode` returns
    a numpy array (or a list of them), not plain Python lists."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        # Typed as Any: sentence-transformers' own overload set for
        # `.encode` resolves to partially-Unknown members under strict
        # pyright, so this stops that unknown-ness from propagating into
        # every caller of this module rather than suppressing it per call.
        self._model: Any = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        # normalize_embeddings=True: unit-norm vectors, so pgvector's `<=>`
        # cosine-distance operator and a plain dot product agree, and
        # `1 - distance` in f1_radio.search is a true cosine similarity.
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in vector] for vector in vectors]


def load_embedding_model(model_name: str = DEFAULT_EMBEDDING_MODEL) -> EmbeddingBackend:
    """Loads a real local sentence-embedding model (downloaded to the local
    HF cache on first use, then reused offline) -- no per-call API cost."""
    return _SentenceTransformerBackend(model_name)


def embed_texts(texts: Sequence[str], backend: EmbeddingBackend) -> list[list[float]]:
    """Embeds a batch of already-cleaned transcript texts. A separate,
    trivial function (rather than calling `backend.encode` directly at
    every call site) so `f1_radio.index_batch`/`f1_radio.search` share one
    place to change if batching/preprocessing ever needs to grow.
    Empty input returns `[]` without invoking the backend -- some
    embedding backends error on a zero-length batch."""
    if not texts:
        return []
    return backend.encode(texts)
