"""CLI argument wiring. The DB-touching command bodies are exercised by the
gated store tests and the verify harness; here we only check parsing routes
to the right handler with the right required args."""

from __future__ import annotations

import pytest

from f1_pipeline.cli import parse_args


def test_replay_requires_round() -> None:
    # --round is not in the recording manifest, so the CLI must demand it.
    with pytest.raises(SystemExit):
        parse_args(["replay", "some/recording"])


def test_replay_parses_to_its_handler() -> None:
    args = parse_args(["replay", "rec", "--round", "3", "--speed", "5"])
    assert args.command == "replay"
    assert args.func.__name__ == "_cmd_replay"
    assert args.round == 3
    assert args.speed == 5.0


def test_ingest_historical_parses() -> None:
    args = parse_args(["ingest-historical", "--season", "2023", "--round", "5"])
    assert args.func.__name__ == "_cmd_ingest_historical"
    assert args.season == 2023
    assert args.round == 5


def test_live_requires_year_and_round() -> None:
    with pytest.raises(SystemExit):
        parse_args(["live", "--year", "2024"])  # missing --round


def test_live_parses() -> None:
    args = parse_args(["live", "--year", "2024", "--round", "1", "--session-type", "race"])
    assert args.func.__name__ == "_cmd_live"
    assert (args.year, args.round, args.session_type) == (2024, 1, "race")


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["frobnicate"])
