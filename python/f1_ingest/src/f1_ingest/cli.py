"""Command-line entrypoint: `f1-record`.

See the reality check in this package's `__init__.py` docstring and
`docs/PHASE0.md`: this can only be *fully* tested during a live
session (~24 weekends/year). `--dry-run` is the only way to smoke-test the
connection outside a race weekend.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Any, cast

from f1_ingest import __version__
from f1_ingest.client import LiveFeedClient, check_live
from f1_ingest.recording_session import RecordingSession
from f1_ingest.topics import default_topics

logger = logging.getLogger("f1_ingest")

DEFAULT_OUT_DIR = Path("data/recordings")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.dry_run:
        return run_dry_run(connect_timeout=args.connect_timeout)
    return run_recorder(args)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="f1-record",
        description=(
            "Record F1's live SignalR timing feed to disk, raw (Phase 0 — no "
            "domain parsing, no database)."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory for recordings (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="override the auto-generated recording directory name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "connect, report whether a session is currently live, then exit "
            "without recording anything"
        ),
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help=(
            "when recording, attempt to connect without F1TV authentication "
            "(may return partial/empty data); --dry-run always skips auth"
        ),
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=60.0,
        help="reconnect if no message is received for this many seconds (0 disables)",
    )
    parser.add_argument(
        "--max-backoff",
        type=float,
        default=60.0,
        help="maximum seconds to wait between reconnect attempts",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=15.0,
        help="seconds to wait for --dry-run's initial connection",
    )
    return parser.parse_args(argv)


def run_dry_run(*, connect_timeout: float) -> int:
    logger.info("Connecting (dry run, f1_ingest v%s)...", __version__)
    # Always skip auth here: the dry run is meant to be a quick,
    # non-interactive connectivity check, and FastF1's real auth flow needs
    # an F1TV subscription plus an interactive browser sign-in - see
    # docs/PHASE0.md. `--no-auth` only affects `run_recorder`.
    snapshot = check_live(no_auth=True, connect_timeout=connect_timeout)
    session_info_raw = snapshot.get("SessionInfo") if snapshot else None

    if isinstance(session_info_raw, dict) and session_info_raw:
        session_info = cast("dict[str, Any]", session_info_raw)
        meeting_info: dict[str, Any] = session_info.get("Meeting") or {}
        meeting = meeting_info.get("Name", "unknown session")
        session_type = session_info.get("Type", "unknown")
        print(f"LIVE: a session appears to be active - {meeting} ({session_type}).")
    else:
        # Expected outside a race weekend - the feed just returns no data.
        print("NOT LIVE: the feed returned no data (expected outside a race weekend).")
    return 0


def run_recorder(args: argparse.Namespace) -> int:
    stop_event = threading.Event()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s; shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    topics = default_topics()
    session = RecordingSession(
        args.out,
        topics=topics,
        recorder_version=__version__,
        session_name=args.session_name,
    )

    backoff = 1.0
    try:
        while not stop_event.is_set():
            client = LiveFeedClient(
                on_subscribe_result=session.on_subscribe_result,
                on_feed_message=session.on_feed_message,
                topics=topics,
                no_auth=args.no_auth,
                idle_timeout=args.idle_timeout,
            )
            try:
                client.run_until(stop_event)
                backoff = 1.0  # a clean run means the feed itself is fine
            except Exception:
                logger.exception("Live feed connection failed")

            if stop_event.is_set():
                break

            logger.warning("Disconnected; reconnecting in %.1fs", backoff)
            stop_event.wait(timeout=backoff)
            backoff = min(backoff * 2, args.max_backoff)
    finally:
        session.close()

    if session.directory is not None:
        logger.info("Recording saved to %s", session.directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
