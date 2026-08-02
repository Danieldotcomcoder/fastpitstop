"""`f1-historical-compare`: run the recording-vs-historical harness for one
session and print the field-level agreement report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from f1_domain import SessionKey, SessionType
from f1_historical_adapter.compare import compare_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a Phase 0 recording against historical parquet for one session."
    )
    parser.add_argument("recording_dir", type=Path, help="Path to the recording directory.")
    parser.add_argument("--historical-root", type=Path, default=Path("data/historical"))
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument(
        "--session-type",
        type=SessionType,
        default=SessionType.RACE,
        choices=list(SessionType),
        help="Domain session type (default: race).",
    )
    args = parser.parse_args(argv)

    session_key = SessionKey(year=args.year, round=args.round, session_type=args.session_type)
    report = compare_session(args.recording_dir, args.historical_root, session_key)
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
