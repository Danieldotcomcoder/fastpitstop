"""CLI argument parsing for `f1-radio fetch`/`transcribe`/`quality-report`.
`main()` itself isn't exercised here since it always hits the real
historical root/cache/database -- see test_fetcher.py/test_discovery.py/
test_transcribe_batch.py for the orchestration logic with everything
injected.
"""

from __future__ import annotations

from pathlib import Path

from f1_backfill.pipeline import DEFAULT_OUT_DIR
from f1_radio.cache import DEFAULT_CACHE_ROOT
from f1_radio.cli import parse_args
from f1_radio.transcription import DEFAULT_COMPUTE_TYPE, DEFAULT_DEVICE, DEFAULT_MODEL_SIZE


def test_fetch_defaults() -> None:
    args = parse_args(["fetch", "--season", "2024"])
    assert args.command == "fetch"
    assert args.season == 2024
    assert args.round is None
    assert args.historical_root == DEFAULT_OUT_DIR
    assert args.cache == DEFAULT_CACHE_ROOT
    assert args.manifest is None
    assert args.refresh is False


def test_fetch_overrides() -> None:
    args = parse_args(
        [
            "fetch",
            "--season",
            "2023",
            "--round",
            "5",
            "--historical-root",
            "custom/historical",
            "--cache",
            "custom/radio_audio",
            "--manifest",
            "custom/manifest.json",
            "--refresh",
        ]
    )
    assert args.season == 2023
    assert args.round == 5
    assert args.historical_root == Path("custom/historical")
    assert args.cache == Path("custom/radio_audio")
    assert args.manifest == Path("custom/manifest.json")
    assert args.refresh is True


def test_transcribe_defaults() -> None:
    args = parse_args(["transcribe", "--season", "2024"])
    assert args.command == "transcribe"
    assert args.season == 2024
    assert args.round is None
    assert args.historical_root == DEFAULT_OUT_DIR
    assert args.cache == DEFAULT_CACHE_ROOT
    assert args.manifest is None
    assert args.model_size == DEFAULT_MODEL_SIZE
    assert args.device == DEFAULT_DEVICE
    assert args.compute_type == DEFAULT_COMPUTE_TYPE
    assert args.workers == 1
    assert args.dsn is None


def test_transcribe_overrides() -> None:
    args = parse_args(
        [
            "transcribe",
            "--season",
            "2023",
            "--round",
            "5",
            "--model-size",
            "medium",
            "--device",
            "cuda",
            "--compute-type",
            "float16",
            "--workers",
            "4",
            "--low-confidence-threshold",
            "0.7",
            "--dsn",
            "postgresql://x/y",
        ]
    )
    assert args.season == 2023
    assert args.round == 5
    assert args.model_size == "medium"
    assert args.device == "cuda"
    assert args.compute_type == "float16"
    assert args.workers == 4
    assert args.low_confidence_threshold == 0.7
    assert args.dsn == "postgresql://x/y"


def test_quality_report_defaults() -> None:
    args = parse_args(["quality-report", "--season", "2024"])
    assert args.command == "quality-report"
    assert args.season == 2024
    assert args.round is None
    assert args.out is None
    assert args.dsn is None


def test_quality_report_overrides() -> None:
    args = parse_args(
        ["quality-report", "--season", "2024", "--round", "3", "--out", "report.md", "--dsn", "x"]
    )
    assert args.round == 3
    assert args.out == Path("report.md")
    assert args.dsn == "x"
