"""The offline batch runner: transcribes every un-transcribed clip in a
season (or one round) and writes results into `radio_transcripts` via a
`TranscriptStore`. Transcription runs OFFLINE/BATCH over already-cached
audio, never on request (CLAUDE.md's Phase 4 invariant) -- this module is
the whole of that batch step; the live API only ever reads what this wrote.

`run_batch` is the pure(ish), fully-injected core -- given an iterable of
`TeamRadioClip` events, a fetch manifest (which clips have cached audio,
from Phase 4.1's `f1_radio.fetcher`) and store/model factories, it does the
work with no hidden dependency on argparse, a season catalog, or a real
model/database. `run_batch_for_season` is the CLI-facing convenience wrapper
that wires discovery + defaults together, matching the split
`f1_radio.fetcher.fetch_clips` / `f1_radio.cli._run_fetch` already
establishes in this package.

**Idempotency**: a clip already carrying a transcript row is skipped before
any model work happens (`store.get(...)`, one cheap lookup per clip) --
transcription is never re-run for a clip that already has one.

**Resumability**: skip decisions are made fresh on every call from the
store's current contents, not from any run-local state, so a run that dies
partway through (crash, Ctrl+C) leaves every completed clip's row durably
written and a re-run picks up exactly where it left off -- no separate
progress file to go stale, unlike `f1_radio.fetcher`'s manifest (that one
needs its own file because a FAILED/NO_URL outcome has nowhere else to live;
a transcript's "am I done" question is answered by the table itself).

**Parallel workers, no double-processing**: `workers=N` runs N clips'
transcriptions concurrently via a thread pool (faster-whisper's underlying
ctranslate2 calls release the GIL during inference, so this is genuine
parallelism, not just I/O overlap) -- CPU-bound but real work, safe on a
local machine within the project's budget. The double-processing guard is
the store's own claim semantics: each worker's `store.insert(...)` is the
CLAIM, atomic against the real table's UNIQUE CONSTRAINT (`ON CONFLICT DO
NOTHING`) or `MemoryTranscriptStore`'s own lock -- if two workers (in this
process, or two separate `f1-radio transcribe` invocations racing against
the same Postgres database) both transcribe the same not-yet-claimed clip,
only one insert lands; the other's model work is wasted CPU time, never a
crash or a duplicate row. A model instance is loaded once per worker thread
(via thread-local storage) and reused across that thread's clips -- loading
one is the expensive part of this pipeline, not the per-clip transcription
call itself.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from f1_domain import TeamRadioClip
from f1_radio.manifest import ClipStatus, FetchManifest, clip_key
from f1_radio.store import TranscriptRecord, TranscriptStore
from f1_radio.transcription import (
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    DEFAULT_MODEL_SIZE,
    WhisperBackend,
    load_model,
    transcribe_clip,
)
from f1_radio.transcription import (
    model_version as compute_model_version,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchReport:
    transcribed: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    """Already had a transcript row -- idempotent skip."""
    no_audio: list[str] = field(default_factory=list[str])
    """No FETCHED manifest entry -- nothing to transcribe yet (fetch first)."""
    failed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    """Transcription itself raised -- one bad audio file never aborts the batch."""


def run_batch(
    events: Iterable[TeamRadioClip],
    *,
    cache_root: Path,
    manifest: FetchManifest,
    store_factory: Callable[[], TranscriptStore],
    model_factory: Callable[[], WhisperBackend] | None = None,
    model_version: str = compute_model_version(),
    workers: int = 1,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> BatchReport:
    """Transcribe every clip in `events` that has fetched audio (per
    `manifest`) and no transcript row yet (per `store_factory()`).

    `store_factory`/`model_factory` are called fresh per unit of work
    (`store_factory` once for the up-front skip check plus once per clip
    written; `model_factory` once per worker thread, cached there) rather
    than sharing one instance across threads -- see the module docstring for
    why each is safe/cheap enough that this is not worth optimizing further.
    """
    resolved_model_factory = model_factory or (lambda: load_model(DEFAULT_MODEL_SIZE))
    report = BatchReport()

    pending: list[tuple[TeamRadioClip, str]] = []
    probe_store = store_factory()
    try:
        for event in events:
            key = clip_key(event.session_key, event.driver, event.captured_at)
            entry = manifest.clips.get(key)
            if entry is None or entry.status != ClipStatus.FETCHED or entry.audio_path is None:
                report.no_audio.append(key)
                continue
            if probe_store.get(event.session_key, event.driver, event.captured_at) is not None:
                report.skipped.append(key)
                continue
            pending.append((event, entry.audio_path))
    finally:
        probe_store.close()

    if not pending:
        return report

    local = threading.local()

    def _model() -> WhisperBackend:
        model = getattr(local, "model", None)
        if model is None:
            model = resolved_model_factory()
            local.model = model
        return model

    def _handle(item: tuple[TeamRadioClip, str]) -> tuple[str, str | None]:
        event, rel_audio_path = item
        key = clip_key(event.session_key, event.driver, event.captured_at)
        try:
            result = transcribe_clip(
                cache_root / rel_audio_path,
                _model(),
                low_confidence_threshold=low_confidence_threshold,
            )
        except Exception as exc:
            logger.warning("Failed to transcribe clip %s: %s", key, exc)
            return key, f"{type(exc).__name__}: {exc}"

        record = TranscriptRecord(
            session_key=event.session_key,
            driver=event.driver,
            captured_at=event.captured_at,
            audio_path=rel_audio_path,
            transcript_text=result.text,
            language=result.language,
            confidence=result.confidence,
            model_version=model_version,
            segments=[
                {
                    "start_s": segment.start_s,
                    "end_s": segment.end_s,
                    "text": segment.text,
                    "confidence": segment.confidence,
                }
                for segment in result.segments
            ],
        )
        store = store_factory()
        try:
            store.insert(record)
        finally:
            store.close()
        return key, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_handle, item) for item in pending]
        for future in as_completed(futures):
            key, error = future.result()
            if error is None:
                report.transcribed.append(key)
            else:
                report.failed.append((key, error))

    return report


def run_batch_for_season(
    *,
    season: int,
    round_filter: int | None,
    historical_root: Path,
    cache_root: Path,
    manifest_path: Path,
    store_factory: Callable[[], TranscriptStore],
    model_size: str = DEFAULT_MODEL_SIZE,
    device: str,
    compute_type: str,
    workers: int,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> BatchReport:
    """The `f1-radio transcribe --season Y` orchestration: wires discovery
    (`f1_radio.discovery`, the same catalog-driven event source `fetch_clips`
    reads) to `run_batch`, deriving the model factory/version together so
    they can never disagree about which model actually ran.
    """
    from f1_radio.discovery import season_team_radio_events

    events = season_team_radio_events(historical_root, season, round_filter=round_filter)
    manifest = FetchManifest.load(manifest_path)
    return run_batch(
        events,
        cache_root=cache_root,
        manifest=manifest,
        store_factory=store_factory,
        model_factory=lambda: load_model(model_size, device=device, compute_type=compute_type),
        model_version=compute_model_version(model_size),
        workers=workers,
        low_confidence_threshold=low_confidence_threshold,
    )
