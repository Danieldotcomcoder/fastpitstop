"""Command-line entrypoint: `f1-backfill`.

`f1-backfill --season 2024` acquires every session of the 2024 season into
`data/historical/` plus an updated `manifest.json`; re-running it makes no
redundant network calls (already-acquired sessions are skipped unless
`--refresh` is passed).

`f1-backfill --season 2025 --positions-only` adds the `car_positions` table
to sessions acquired before it existed, from FastF1's cache (see
`f1_backfill.positions`). With `--round`, `--session` and `--uncatalogued`
it targets one session the catalog does not list: a live-ingested race
whose car positions only exist in F1's post-session archive.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from f1_backfill.fastf1_source import configure_cache
from f1_backfill.pipeline import DEFAULT_OUT_DIR, run_backfill
from f1_backfill.positions import run_positions

logger = logging.getLogger("f1_backfill")

DEFAULT_FASTF1_CACHE_DIR = Path("data/fastf1_cache")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    configure_cache(args.fastf1_cache)
    if args.positions_only:
        return _positions(args)
    report = run_backfill(
        args.season,
        round_filter=args.round,
        out_dir=args.out,
        catalog_path=args.catalog,
        refresh=args.refresh,
    )

    logger.info(
        "Done: %d acquired, %d skipped (already in catalog), %d failed",
        len(report.acquired),
        len(report.skipped),
        len(report.failed),
    )
    for key, error in report.failed:
        logger.error("  %s: %s", key.catalog_key, error)
    return 1 if report.failed else 0


def _positions(args: argparse.Namespace) -> int:
    report = run_positions(
        args.season,
        round_filter=args.round,
        session_filter=args.session,
        out_dir=args.out,
        catalog_path=args.catalog,
        refresh=args.refresh,
        include_uncatalogued=args.uncatalogued,
    )
    logger.info(
        "Done: positions written for %d sessions, %d skipped, %d failed",
        len(report.written),
        len(report.skipped),
        len(report.failed),
    )
    for key, error in report.failed:
        logger.error("  %s: %s", key.catalog_key, error)
    return 1 if report.failed else 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="f1-backfill",
        description=(
            "Acquire and cache F1 sessions (FastF1 + OpenF1) as clean historical "
            "parquet tables, feeding the Phase 2 analytics kernel directly."
        ),
    )
    parser.add_argument("--season", type=int, required=True, help="season year, e.g. 2024")
    parser.add_argument(
        "--round", type=int, default=None, help="restrict to one round number (default: all rounds)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory for partitioned parquet (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="override the catalog/manifest path (default: <out>/manifest.json)",
    )
    parser.add_argument(
        "--fastf1-cache",
        type=Path,
        default=DEFAULT_FASTF1_CACHE_DIR,
        help=f"FastF1 on-disk cache directory (default: {DEFAULT_FASTF1_CACHE_DIR})",
    )
    parser.add_argument(
        "--positions-only",
        action="store_true",
        help=(
            "only write the car_positions table, from FastF1's cache, for sessions "
            "already in the catalog"
        ),
    )
    parser.add_argument(
        "--session",
        default=None,
        help="with --positions-only: restrict to one session name, e.g. Race",
    )
    parser.add_argument(
        "--uncatalogued",
        action="store_true",
        help=(
            "with --positions-only: also write sessions the catalog does not list "
            "(a live-ingested session served from the store)"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-pull sessions already present in the catalog instead of skipping them",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
