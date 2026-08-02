"""Command-line entrypoint: `f1-replay`.

Prints a recording back out as JSONL on stdout, in the same relative timing
downstream code would have seen it live (see `f1_replay.replayer` for the
timing model). Piping this into a Phase 1 pipeline process is the point —
it's what makes weekday development against ~80 past races possible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from f1_replay.replayer import replay
from f1_replay.sinks import jsonl_sink


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    replay(
        args.recording,
        jsonl_sink(sys.stdout),
        speed=args.speed,
        delay=args.delay,
    )
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="f1-replay",
        description=(
            "Replay an f1_recording recording as JSONL on stdout, in the same "
            "relative timing a live consumer would have seen it."
        ),
    )
    parser.add_argument(
        "recording",
        type=Path,
        help="path to a recording directory (containing manifest.json/subscribe.json/stream.jsonl)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="time multiplier: 1=real time, 100=100x fast, 0=as-fast-as-possible (default: 1.0)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="hold every emission back by this many fixed seconds (default: 0.0)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
