"""Command-line entrypoint: `f1-radio`.

`f1-radio fetch --season 2024` downloads every backfilled 2024 session's
team-radio audio into `data/radio_audio/` plus an updated manifest;
re-running it makes no redundant network calls (already-fetched clips are
skipped unless `--refresh` is passed).

`f1-radio transcribe --season 2024 [--workers N]` batch-transcribes every
fetched-but-not-yet-transcribed clip (Phase 4.2) via local faster-whisper,
writing into `radio_transcripts` (`F1_STORE_DSN`, reused from `f1_store`).
Idempotent, resumable, safely parallelizable -- see
`f1_radio.transcribe_batch`'s module docstring for the full contract.

`f1-radio quality-report --season 2024` summarizes a season's transcripts
(count, confidence distribution, flagged low-confidence clips, language
breakdown) as markdown, printed to stdout or written with `--out`.

`f1-radio index --season 2024` (Phase 4.3) embeds every already-transcribed
clip locally and backfills race context (lap, position, track status) onto
its `radio_transcripts` row -- idempotent and resumable, see
`f1_radio.index_batch`'s module docstring.

`f1-radio search "query" [--driver 44] [--season 2024] ...` (Phase 4.3)
semantically searches the indexed corpus, optionally narrowed by structured
filters -- see `f1_radio.search`'s module docstring.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from f1_backfill.pipeline import DEFAULT_OUT_DIR
from f1_domain import SessionType
from f1_radio.cache import DEFAULT_CACHE_ROOT
from f1_radio.discovery import season_team_radio_events
from f1_radio.embeddings import DEFAULT_EMBEDDING_MODEL, load_embedding_model
from f1_radio.fetcher import FetchReport, fetch_clips
from f1_radio.index_batch import DEFAULT_BATCH_SIZE, IndexReport, run_index_batch_for_season
from f1_radio.manifest import MANIFEST_FILENAME
from f1_radio.quality_report import build_quality_report, render_markdown
from f1_radio.search import PostgresSearchIndex, SearchFilters, search_transcripts
from f1_radio.store import PostgresTranscriptStore
from f1_radio.transcribe_batch import BatchReport, run_batch_for_season
from f1_radio.transcription import (
    DEFAULT_COMPUTE_TYPE,
    DEFAULT_DEVICE,
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    DEFAULT_MODEL_SIZE,
)

logger = logging.getLogger("f1_radio")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.command == "fetch":
        return _run_fetch(args)
    if args.command == "transcribe":
        return _run_transcribe(args)
    if args.command == "quality-report":
        return _run_quality_report(args)
    if args.command == "index":
        return _run_index(args)
    if args.command == "search":
        return _run_search(args)
    raise AssertionError(f"unhandled command: {args.command}")  # argparse guarantees a match


def _run_fetch(args: argparse.Namespace) -> int:
    manifest_path = args.manifest if args.manifest is not None else args.cache / MANIFEST_FILENAME
    events = season_team_radio_events(args.historical_root, args.season, round_filter=args.round)
    report = fetch_clips(
        events,
        cache_root=args.cache,
        manifest_path=manifest_path,
        refresh=args.refresh,
    )
    _log_fetch_report(report)
    return 0


def _run_transcribe(args: argparse.Namespace) -> int:
    from f1_store.config import connect

    manifest_path = args.manifest if args.manifest is not None else args.cache / MANIFEST_FILENAME
    report = run_batch_for_season(
        season=args.season,
        round_filter=args.round,
        historical_root=args.historical_root,
        cache_root=args.cache,
        manifest_path=manifest_path,
        store_factory=lambda: PostgresTranscriptStore(connect(args.dsn)),
        model_size=args.model_size,
        device=args.device,
        compute_type=args.compute_type,
        workers=args.workers,
        low_confidence_threshold=args.low_confidence_threshold,
    )
    _log_transcribe_report(report)
    return 0


def _run_quality_report(args: argparse.Namespace) -> int:
    from f1_store.config import connect

    with connect(args.dsn) as conn:
        from f1_radio.store import list_transcripts_for_season

        records = list_transcripts_for_season(conn, args.season, round_filter=args.round)
    report = build_quality_report(records, season=args.season)
    markdown = render_markdown(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        logger.info("Wrote quality report to %s", args.out)
    else:
        print(markdown)
    return 0


def _run_index(args: argparse.Namespace) -> int:
    from f1_store.config import connect

    report = run_index_batch_for_season(
        season=args.season,
        round_filter=args.round,
        historical_root=args.historical_root,
        store_factory=lambda: PostgresTranscriptStore(connect(args.dsn)),
        model_name=args.model_name,
        batch_size=args.batch_size,
    )
    _log_index_report(report)
    return 0


def _run_search(args: argparse.Namespace) -> int:
    from f1_store.config import connect

    session_type = SessionType(args.session_type) if args.session_type is not None else None
    filters = SearchFilters(
        driver=args.driver,
        season=args.season,
        round=args.round,
        session_type=session_type,
        lap_number=args.lap,
        track_status=args.track_status,
        captured_after=_parse_iso(args.captured_after),
        captured_before=_parse_iso(args.captured_before),
        keyword=args.keyword,
    )
    embedding_backend = load_embedding_model(args.model_name) if args.query.strip() else None
    with connect(args.dsn) as conn:
        results = search_transcripts(
            args.query,
            index=PostgresSearchIndex(conn),
            embedding_backend=embedding_backend,
            filters=filters,
            limit=args.limit,
        )
    for result in results:
        record = result.record
        print(
            f"[{result.similarity:.3f}] driver={record.driver or '?'} "
            f"session={record.session_key.year}-R{record.session_key.round:02d}-"
            f"{record.session_key.session_type.value} "
            f"lap={record.lap_number if record.lap_number is not None else '?'} "
            f"pos={record.position if record.position is not None else '?'} "
            f"status={record.track_status or '?'} "
            f"confidence={record.confidence if record.confidence is not None else '?'}"
        )
        print(f"    {record.transcript_text}")
    if not results:
        print("No results.")
    return 0


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _log_index_report(report: IndexReport) -> None:
    logger.info(
        "Done: %d indexed, %d skipped (already indexed), %d no transcript, "
        "%d empty text, %d failed",
        len(report.indexed),
        len(report.skipped),
        len(report.no_transcript),
        len(report.empty_text),
        len(report.failed),
    )
    for key, error in report.failed:
        logger.warning("  %s: %s", key, error)


def _log_fetch_report(report: FetchReport) -> None:
    logger.info(
        "Done: %d fetched, %d skipped (already in manifest), %d no url, %d failed",
        len(report.fetched),
        len(report.skipped),
        len(report.no_url),
        len(report.failed),
    )
    for key, error in report.failed:
        logger.warning("  %s: %s", key, error)


def _log_transcribe_report(report: BatchReport) -> None:
    logger.info(
        "Done: %d transcribed, %d skipped (already transcribed), %d no audio, %d failed",
        len(report.transcribed),
        len(report.skipped),
        len(report.no_audio),
        len(report.failed),
    )
    for key, error in report.failed:
        logger.warning("  %s: %s", key, error)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    # Every subparser is built inline here (rather than via helper functions
    # taking the subparsers action as a parameter) so nothing has to name
    # `argparse._SubParsersAction`'s type -- it's private, and pyright's
    # strict mode (this workspace's setting) flags cross-function use of a
    # private type. Matches `f1_pipeline.cli.parse_args`'s own layout.
    parser = argparse.ArgumentParser(
        prog="f1-radio",
        description="Acquire and transcribe team-radio audio referenced by TeamRadioClip events.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser(
        "fetch", help="download a season's team-radio audio into the local cache"
    )
    fetch.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    fetch.add_argument(
        "--round", type=int, default=None, help="restrict to one round number (default: all rounds)"
    )
    fetch.add_argument(
        "--historical-root",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=(
            "f1-backfill output directory to read TeamRadioClip events from "
            f"(default: {DEFAULT_OUT_DIR})"
        ),
    )
    fetch.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help=f"output directory for the audio cache (default: {DEFAULT_CACHE_ROOT})",
    )
    fetch.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"override the manifest path (default: <cache>/{MANIFEST_FILENAME})",
    )
    fetch.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch clips already present in the manifest instead of skipping them",
    )

    transcribe = subparsers.add_parser(
        "transcribe",
        help="batch-transcribe fetched-but-not-yet-transcribed clips via local faster-whisper",
    )
    transcribe.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    transcribe.add_argument(
        "--round", type=int, default=None, help="restrict to one round number (default: all rounds)"
    )
    transcribe.add_argument(
        "--historical-root",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=(
            "f1-backfill output directory to read TeamRadioClip events from "
            f"(default: {DEFAULT_OUT_DIR})"
        ),
    )
    transcribe.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help=f"the audio cache to read clips from (default: {DEFAULT_CACHE_ROOT})",
    )
    transcribe.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=f"override the manifest path (default: <cache>/{MANIFEST_FILENAME})",
    )
    transcribe.add_argument(
        "--model-size",
        default=DEFAULT_MODEL_SIZE,
        help=f"faster-whisper model size (default: {DEFAULT_MODEL_SIZE})",
    )
    transcribe.add_argument(
        "--device", default=DEFAULT_DEVICE, help=f"'cpu' or 'cuda' (default: {DEFAULT_DEVICE})"
    )
    transcribe.add_argument(
        "--compute-type",
        default=DEFAULT_COMPUTE_TYPE,
        help=f"ctranslate2 compute type, e.g. int8/float16 (default: {DEFAULT_COMPUTE_TYPE})",
    )
    transcribe.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of clips to transcribe concurrently (default: 1)",
    )
    transcribe.add_argument(
        "--low-confidence-threshold",
        type=float,
        default=DEFAULT_LOW_CONFIDENCE_THRESHOLD,
        help=(
            "confidence below which a transcript is flagged "
            f"(default: {DEFAULT_LOW_CONFIDENCE_THRESHOLD})"
        ),
    )
    transcribe.add_argument(
        "--dsn",
        default=None,
        help="libpq connection string; overrides $F1_STORE_DSN and PG* env vars.",
    )

    quality_report = subparsers.add_parser(
        "quality-report", help="summarize a season's transcript quality as markdown"
    )
    quality_report.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    quality_report.add_argument(
        "--round", type=int, default=None, help="restrict to one round number (default: all rounds)"
    )
    quality_report.add_argument(
        "--out", type=Path, default=None, help="write markdown here instead of stdout"
    )
    quality_report.add_argument(
        "--dsn",
        default=None,
        help="libpq connection string; overrides $F1_STORE_DSN and PG* env vars.",
    )

    index = subparsers.add_parser(
        "index",
        help="embed already-transcribed clips + backfill race context (Phase 4.3)",
    )
    index.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    index.add_argument(
        "--round", type=int, default=None, help="restrict to one round number (default: all rounds)"
    )
    index.add_argument(
        "--historical-root",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=(
            "f1-backfill output directory to read the season's events from "
            f"(default: {DEFAULT_OUT_DIR})"
        ),
    )
    index.add_argument(
        "--model-name",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"sentence-transformers model name (default: {DEFAULT_EMBEDDING_MODEL})",
    )
    index.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"clips embedded per model call (default: {DEFAULT_BATCH_SIZE})",
    )
    index.add_argument(
        "--dsn",
        default=None,
        help="libpq connection string; overrides $F1_STORE_DSN and PG* env vars.",
    )

    search = subparsers.add_parser(
        "search", help="semantic/hybrid/structured search over the indexed transcript corpus"
    )
    search.add_argument("query", help='free-text query, e.g. "front tyres gone"; may be empty')
    search.add_argument("--driver", default=None, help="restrict to one driver number, e.g. 44")
    search.add_argument("--season", type=int, default=None, help="restrict to one season")
    search.add_argument("--round", type=int, default=None, help="restrict to one round number")
    search.add_argument(
        "--session-type",
        default=None,
        choices=[value.value for value in SessionType],
        help="restrict to one session type",
    )
    search.add_argument("--lap", type=int, default=None, help="restrict to one lap number")
    search.add_argument(
        "--track-status",
        default=None,
        help="restrict to one track status at capture time, e.g. sc/vsc/yellow/green",
    )
    search.add_argument(
        "--captured-after", default=None, help="ISO 8601 timestamp lower bound (inclusive)"
    )
    search.add_argument(
        "--captured-before", default=None, help="ISO 8601 timestamp upper bound (inclusive)"
    )
    search.add_argument(
        "--keyword", default=None, help="require this literal substring in the transcript text"
    )
    search.add_argument("--limit", type=int, default=10, help="max results (default: 10)")
    search.add_argument(
        "--model-name",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"sentence-transformers model name (default: {DEFAULT_EMBEDDING_MODEL})",
    )
    search.add_argument(
        "--dsn",
        default=None,
        help="libpq connection string; overrides $F1_STORE_DSN and PG* env vars.",
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
