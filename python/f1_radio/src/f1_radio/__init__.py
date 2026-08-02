"""f1_radio: Phase 4's team-radio corpus pipeline. Consumes the
`TeamRadioClip` events Phase 1 already emits: a local audio cache
(`fetcher`, keyed deterministically by `cache`), the transcript store
(`store`), local batch transcription via faster-whisper (`transcription`,
`transcribe_batch`), a per-season quality report (`quality_report`), and
(Phase 4.3) local sentence embeddings (`embeddings`) + race-context
enrichment (`context`) indexed onto the transcript store (`index_batch`)
and queried via semantic/hybrid/structured search (`search`).
"""

from f1_radio.cache import DEFAULT_CACHE_ROOT, clip_cache_path
from f1_radio.context import ClipContext, SessionContext
from f1_radio.discovery import season_session_keys, season_team_radio_events
from f1_radio.domain import RadioClip, radio_clip
from f1_radio.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIM,
    EmbeddingBackend,
    embed_texts,
    embedding_model_version,
    load_embedding_model,
)
from f1_radio.fetcher import DownloadError, FetchReport, fetch_clips
from f1_radio.index_batch import IndexReport, run_index_batch, run_index_batch_for_season
from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key
from f1_radio.quality_report import (
    FlaggedClip,
    QualityReport,
    build_quality_report,
    render_markdown,
)
from f1_radio.search import (
    MemorySearchIndex,
    PostgresSearchIndex,
    SearchFilters,
    SearchIndex,
    SearchResult,
    search_transcripts,
)
from f1_radio.store import (
    MemoryTranscriptStore,
    PostgresTranscriptStore,
    TranscriptRecord,
    TranscriptStore,
    get_transcript,
    insert_transcript,
    list_transcripts_for_season,
)
from f1_radio.transcribe_batch import BatchReport, run_batch, run_batch_for_season
from f1_radio.transcription import (
    TranscriptionResult,
    TranscriptSegment,
    WhisperBackend,
    load_model,
    transcribe_clip,
)

__all__ = [
    "DEFAULT_CACHE_ROOT",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "BatchReport",
    "ClipContext",
    "ClipEntry",
    "ClipStatus",
    "DownloadError",
    "EmbeddingBackend",
    "FetchManifest",
    "FetchReport",
    "FlaggedClip",
    "IndexReport",
    "MemorySearchIndex",
    "MemoryTranscriptStore",
    "PostgresSearchIndex",
    "PostgresTranscriptStore",
    "QualityReport",
    "RadioClip",
    "SearchFilters",
    "SearchIndex",
    "SearchResult",
    "SessionContext",
    "TranscriptRecord",
    "TranscriptSegment",
    "TranscriptStore",
    "TranscriptionResult",
    "WhisperBackend",
    "build_quality_report",
    "clip_cache_path",
    "clip_key",
    "embed_texts",
    "embedding_model_version",
    "fetch_clips",
    "get_transcript",
    "insert_transcript",
    "list_transcripts_for_season",
    "load_embedding_model",
    "load_model",
    "radio_clip",
    "render_markdown",
    "run_batch",
    "run_batch_for_season",
    "run_index_batch",
    "run_index_batch_for_season",
    "search_transcripts",
    "season_session_keys",
    "season_team_radio_events",
    "transcribe_clip",
]

__version__ = "0.0.1"
