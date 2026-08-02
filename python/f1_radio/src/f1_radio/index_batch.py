"""The offline indexing batch runner (Phase 4.3): for every already-
TRANSCRIBED clip with non-empty text, computes its race context
(`f1_radio.context`) and a local sentence embedding (`f1_radio.embeddings`),
and backfills both onto its `radio_transcripts` row via
`TranscriptStore.set_embedding`. Mirrors `f1_radio.transcribe_batch.
run_batch`'s shape and guarantees:

**Idempotent**: a clip whose stored `embedding_model_version` already
matches the one this run would produce is skipped before any embedding work
happens -- re-indexing with the same model changes nothing.

**Resumable**: skip decisions come fresh from the store's current contents
on every call, not from run-local state, so a run that dies partway through
leaves every completed clip's embedding durably written and a re-run picks
up exactly where it left off.

`run_index_batch` is the fully-injected core, taking `(TeamRadioClip,
ClipContext)` pairs rather than raw events -- context computation
(`f1_radio.context.SessionContext`, built once per SESSION from that
session's full event stream) is the orchestration layer's job, kept out of
this function so it stays testable with hand-built contexts and no event
stream at all. `run_index_batch_for_season` is that orchestration: one
`SessionContext` per backfilled session, the embedding model loaded once
and reused across every session (unlike per-clip transcription, embedding a
batch of already-short transcript strings is cheap -- the expensive load is
the model itself).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from f1_domain import TeamRadioClip
from f1_radio.context import ClipContext
from f1_radio.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingBackend,
    embed_texts,
    embedding_model_version,
    load_embedding_model,
)
from f1_radio.manifest import clip_key
from f1_radio.store import TranscriptStore

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 32


@dataclass
class IndexReport:
    indexed: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    """Already carries an embedding from this exact model version."""
    no_transcript: list[str] = field(default_factory=list[str])
    """No transcript row yet -- transcribe (4.2) before indexing (4.3)."""
    empty_text: list[str] = field(default_factory=list[str])
    """A transcript row exists but has no text (silence/no-speech) -- there
    is nothing to embed."""
    failed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    """Embedding itself raised for the whole batch it was in -- one bad
    batch never aborts the run."""


def run_index_batch(
    items: Iterable[tuple[TeamRadioClip, ClipContext]],
    *,
    store_factory: Callable[[], TranscriptStore],
    embedding_backend_factory: Callable[[], EmbeddingBackend] | None = None,
    model_version: str = embedding_model_version(),
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IndexReport:
    """Indexes every `(clip, context)` pair whose clip already has a
    non-empty transcript and no embedding from `model_version` yet.

    `embedding_backend_factory` is called AT MOST ONCE, lazily -- if every
    pending clip turns out to be skipped/empty, no model ever loads. A
    caller indexing several sessions in one run (`run_index_batch_for_season`)
    should pass a factory that caches the loaded model itself, the same way
    `run_batch_for_season` derives one model per worker thread rather than
    reloading per clip.
    """
    resolved_backend_factory = embedding_backend_factory or (
        lambda: load_embedding_model(DEFAULT_EMBEDDING_MODEL)
    )
    report = IndexReport()

    pending: list[tuple[TeamRadioClip, ClipContext, str]] = []
    probe_store = store_factory()
    try:
        for clip, ctx in items:
            key = clip_key(clip.session_key, clip.driver, clip.captured_at)
            existing = probe_store.get(clip.session_key, clip.driver, clip.captured_at)
            if existing is None:
                report.no_transcript.append(key)
                continue
            if existing.embedding_model_version == model_version:
                report.skipped.append(key)
                continue
            text = (existing.transcript_text or "").strip()
            if not text:
                report.empty_text.append(key)
                continue
            pending.append((clip, ctx, text))
    finally:
        probe_store.close()

    if not pending:
        return report

    backend = resolved_backend_factory()
    for chunk in _batched(pending, batch_size):
        texts = [text for _, _, text in chunk]
        try:
            vectors = embed_texts(texts, backend)
        except Exception as exc:  # one bad batch must not abort the run
            error = f"{type(exc).__name__}: {exc}"
            for clip, _ctx, _text in chunk:
                report.failed.append(
                    (clip_key(clip.session_key, clip.driver, clip.captured_at), error)
                )
            continue

        for (clip, ctx, _text), vector in zip(chunk, vectors, strict=True):
            key = clip_key(clip.session_key, clip.driver, clip.captured_at)
            write_store = store_factory()
            try:
                write_store.set_embedding(
                    clip.session_key,
                    clip.driver,
                    clip.captured_at,
                    embedding=vector,
                    embedding_model_version=model_version,
                    lap_number=ctx.lap_number,
                    position=ctx.position,
                    track_status=ctx.track_status.value if ctx.track_status is not None else None,
                    track_status_flags=[flag.value for flag in ctx.track_status_flags],
                )
            finally:
                write_store.close()
            report.indexed.append(key)

    return report


def run_index_batch_for_season(
    *,
    season: int,
    round_filter: int | None,
    historical_root: Path,
    store_factory: Callable[[], TranscriptStore],
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IndexReport:
    """The `f1-radio index --season Y` orchestration: for every backfilled
    session in the season, builds ONE `SessionContext` from that session's
    full event stream (context needs laps/positions/track-status, not just
    the `TeamRadioClip`s `f1_radio.discovery.season_team_radio_events`
    yields, so this loads the full stream itself rather than reusing that
    narrower helper) and runs `run_index_batch` over its clips. The
    embedding model loads at most once across the whole season, cached in
    a closure and reused for every session's `run_index_batch` call.
    """
    from f1_historical_adapter import events_from_parquet
    from f1_radio.context import SessionContext
    from f1_radio.discovery import season_session_keys

    resolved_model_version = embedding_model_version(model_name)
    cached_backend: EmbeddingBackend | None = None

    def _backend() -> EmbeddingBackend:
        nonlocal cached_backend
        if cached_backend is None:
            cached_backend = load_embedding_model(model_name)
        return cached_backend

    combined = IndexReport()
    for key in season_session_keys(historical_root, season, round_filter=round_filter):
        try:
            events = list(events_from_parquet(historical_root, key))
        except FileNotFoundError:
            logger.warning("Catalog lists %s but its historical data is missing; skipping", key)
            continue
        clips = [event for event in events if isinstance(event, TeamRadioClip)]
        if not clips:
            continue
        session_context = SessionContext(events)
        items = [(clip, session_context.at(clip.driver, clip.captured_at)) for clip in clips]
        report = run_index_batch(
            items,
            store_factory=store_factory,
            embedding_backend_factory=_backend,
            model_version=resolved_model_version,
            batch_size=batch_size,
        )
        _merge_into(combined, report)
    return combined


def _merge_into(combined: IndexReport, report: IndexReport) -> None:
    combined.indexed.extend(report.indexed)
    combined.skipped.extend(report.skipped)
    combined.no_transcript.extend(report.no_transcript)
    combined.empty_text.extend(report.empty_text)
    combined.failed.extend(report.failed)


def _batched(
    items: list[tuple[TeamRadioClip, ClipContext, str]], size: int
) -> Iterable[list[tuple[TeamRadioClip, ClipContext, str]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
