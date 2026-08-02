"""CLI argument parsing for `f1-backfill`. `main()` itself isn't exercised
here since it always hits real fastf1/OpenF1 - see test_pipeline.py for the
orchestration logic with everything injected, and
test_integration_network.py for one real end-to-end pull.
"""

from __future__ import annotations

from pathlib import Path

from f1_backfill.cli import DEFAULT_FASTF1_CACHE_DIR, parse_args
from f1_backfill.pipeline import DEFAULT_OUT_DIR


def test_defaults() -> None:
    args = parse_args(["--season", "2024"])
    assert args.season == 2024
    assert args.round is None
    assert args.out == DEFAULT_OUT_DIR
    assert args.catalog is None
    assert args.fastf1_cache == DEFAULT_FASTF1_CACHE_DIR
    assert args.refresh is False


def test_overrides() -> None:
    args = parse_args(
        [
            "--season",
            "2023",
            "--round",
            "5",
            "--out",
            "custom/historical",
            "--catalog",
            "custom/manifest.json",
            "--fastf1-cache",
            "custom/cache",
            "--refresh",
        ]
    )
    assert args.season == 2023
    assert args.round == 5
    assert args.out == Path("custom/historical")
    assert args.catalog == Path("custom/manifest.json")
    assert args.fastf1_cache == Path("custom/cache")
    assert args.refresh is True
