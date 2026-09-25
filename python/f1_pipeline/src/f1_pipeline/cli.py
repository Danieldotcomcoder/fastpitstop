"""`f1-pipeline`: the Phase 1 entry point, wiring each source to Postgres.

    f1-pipeline replay <recording> --round N        # f1_replay -> pipeline -> DB
    f1-pipeline ingest-historical --season Y         # adapter  -> pipeline -> DB
    f1-pipeline live --year Y --round N --session-type race   # feed -> pipeline -> DB
    f1-pipeline archive-positions [--watch SECONDS]   # F1 archive -> positions table

The first three converge on the SAME pipeline (`run`/`run_events`);
`archive-positions` fills in what the live feed withholds (see
`f1_pipeline.archive`). The DB target
comes from `F1_STORE_DSN`/PG* env (or `--dsn`); the schema is applied
(idempotently) before each run. Live imports the socket stack lazily, so
`replay`/`ingest-historical` work without the live extras.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from f1_domain import SessionType
from f1_pipeline import __version__
from f1_pipeline.pipeline import Pipeline, run, run_events
from f1_pipeline.replay_source import replay_records
from f1_pipeline.session import discover_historical_sessions, session_key, session_type_from_name
from f1_pipeline.session_identity import session_key_from_snapshot
from f1_recording import RecordingReader
from f1_store import PostgresSink, apply_migrations
from f1_store.config import connect

logger = logging.getLogger("f1_pipeline")

DEFAULT_HISTORICAL_ROOT = Path("data/historical")
DEFAULT_RECORDINGS_OUT = Path("data/recordings")
DEFAULT_FASTF1_CACHE = Path("data/fastf1_cache")
_SESSION_TYPE_CHOICES = [t.value for t in SessionType]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)
    return args.func(args)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="f1-pipeline", description=__doc__)
    parser.add_argument("--dsn", default=None, help="Postgres DSN; overrides $F1_STORE_DSN / PG*.")
    parser.add_argument("--batch-size", type=int, default=10_000, help="sink COPY batch size")
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay", help="replay a recording into Postgres")
    replay.add_argument("recording", type=Path)
    replay.add_argument(
        "--round", type=int, required=True, help="championship round (not in the recording)"
    )
    replay.add_argument("--year", type=int, default=None, help="override the recording's year")
    replay.add_argument(
        "--session-type", default=None, help="override the recording's session type"
    )
    replay.add_argument(
        "--speed", type=float, default=0.0, help="replay speed (0 = as fast as possible)"
    )
    replay.add_argument(
        "--delay", type=float, default=0.0, help="constant delay offset (Delay Sync)"
    )
    replay.set_defaults(func=_cmd_replay)

    hist = sub.add_parser("ingest-historical", help="ingest historical parquet into Postgres")
    hist.add_argument("--season", type=int, required=True)
    hist.add_argument("--round", type=int, default=None, help="restrict to one round")
    hist.add_argument("--session-type", default=None, help="restrict to one session type")
    hist.add_argument("--historical-root", type=Path, default=DEFAULT_HISTORICAL_ROOT)
    hist.add_argument(
        "--include-telemetry", action="store_true", help="also emit CarTelemetry (large)"
    )
    hist.set_defaults(func=_cmd_ingest_historical)

    live = sub.add_parser("live", help="record the live feed and ingest it into Postgres")
    live.add_argument(
        "--year",
        type=int,
        default=None,
        help="override the season the feed reports (default: read it from SessionInfo)",
    )
    live.add_argument(
        "--round",
        type=int,
        default=None,
        help="override the round the feed reports (default: Meeting.Number from SessionInfo)",
    )
    live.add_argument(
        "--session-type",
        default=None,
        choices=_SESSION_TYPE_CHOICES,
        help="override the session the feed reports (default: read it from SessionInfo)",
    )
    live.add_argument("--out", type=Path, default=DEFAULT_RECORDINGS_OUT, help="raw recording dir")
    live.add_argument("--session-name", default=None)
    live.add_argument("--no-auth", action="store_true")
    live.add_argument("--idle-timeout", type=float, default=60.0)
    live.add_argument("--max-backoff", type=float, default=60.0)
    live.set_defaults(func=_cmd_live)

    archive = sub.add_parser(
        "archive-positions",
        help="import finished store sessions' car positions from F1's archive",
    )
    archive.add_argument("--historical-root", type=Path, default=DEFAULT_HISTORICAL_ROOT)
    archive.add_argument("--fastf1-cache", type=Path, default=DEFAULT_FASTF1_CACHE)
    archive.add_argument(
        "--watch",
        type=float,
        default=None,
        metavar="SECONDS",
        help="keep running, one pass every SECONDS (default: one pass, then exit)",
    )
    archive.add_argument(
        "--settle-minutes",
        type=float,
        default=90.0,
        help="only read a session's archive once it has been quiet this long",
    )
    archive.add_argument(
        "--max-age-days", type=float, default=30.0, help="stop retrying sessions older than this"
    )
    archive.set_defaults(func=_cmd_archive_positions)

    return parser.parse_args(argv)


def _cmd_replay(args: argparse.Namespace) -> int:
    reader = RecordingReader(args.recording)
    manifest = reader.manifest
    year = args.year if args.year is not None else manifest.year
    stype = args.session_type if args.session_type is not None else manifest.session_type
    key = session_key(year=year, round=args.round, session_type=stype)

    logger.info(
        "Replaying %s as %s round %s %s",
        args.recording,
        key.year,
        key.round,
        key.session_type.value,
    )
    with connect(args.dsn) as conn:
        apply_migrations(conn)
        with PostgresSink(conn, batch_size=args.batch_size) as sink:
            metrics = run(
                replay_records(args.recording, speed=args.speed, delay=args.delay),
                sink,
                session_key=key,
            )
    print(metrics.render())
    return 0


def _cmd_ingest_historical(args: argparse.Namespace) -> int:
    from f1_historical_adapter import events_from_parquet

    keys = discover_historical_sessions(args.historical_root, args.season, round_filter=args.round)
    if args.session_type is not None:
        wanted = session_type_from_name(args.session_type)
        keys = [k for k in keys if k.session_type == wanted]
    if not keys:
        logger.warning(
            "No historical sessions found under %s for season %s", args.historical_root, args.season
        )
        return 1

    with connect(args.dsn) as conn:
        apply_migrations(conn)
        for key in keys:
            with PostgresSink(conn, batch_size=args.batch_size) as sink:
                metrics = run_events(
                    events_from_parquet(
                        args.historical_root, key, include_telemetry=args.include_telemetry
                    ),
                    sink,
                )
            print(f"{key.year} round {key.round} {key.session_type.value}:")
            print(metrics.render())
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    # Lazy import: the live socket stack (fastf1/signalrcore) is only needed
    # here, and only ever exercised during a real session (~24 weekends/yr).
    import signal
    import threading
    from types import FrameType

    from f1_ingest.client import LiveFeedClient
    from f1_ingest.recording_session import RecordingSession
    from f1_ingest.topics import default_topics

    topics = default_topics()

    stop_event = threading.Event()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s; shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    with connect(args.dsn) as conn:
        apply_migrations(conn)
        sink = PostgresSink(conn, batch_size=args.batch_size)
        session = RecordingSession(
            args.out, topics=topics, recorder_version=__version__, session_name=args.session_name
        )
        # The pipeline cannot exist until we know WHICH session this is, and
        # only the feed knows that. Built on the first subscribe snapshot and
        # reused across reconnects, so a dropped connection never re-keys a
        # session mid-race.
        state: dict[str, Pipeline] = {}

        def on_subscribe(snapshot: dict[str, Any]) -> None:
            session.on_subscribe_result(snapshot)  # persist raw (+ reconnect markers)
            pipeline = state.get("pipeline")
            if pipeline is None:
                key = session_key_from_snapshot(
                    snapshot,
                    year=args.year,
                    round_number=args.round,
                    session_type=args.session_type,
                )
                logger.info("Ingesting %s", key)
                pipeline = Pipeline(key, sink)
                state["pipeline"] = pipeline
            pipeline.seed(snapshot)

        def on_feed(topic: str, payload: Any) -> None:
            record = session.on_feed_message(topic, payload)
            pipeline = state.get("pipeline")
            if pipeline is None:
                # A feed message before any snapshot: the recorder itself
                # refuses this, so it should be unreachable. Drop the event
                # rather than guess a session key for it.
                return
            pipeline.process(record)

        backoff = 1.0
        try:
            while not stop_event.is_set():
                client = LiveFeedClient(
                    on_subscribe_result=on_subscribe,
                    on_feed_message=on_feed,
                    topics=topics,
                    no_auth=args.no_auth,
                    idle_timeout=args.idle_timeout,
                )
                try:
                    client.run_until(stop_event)
                    backoff = 1.0
                except Exception:
                    logger.exception("Live feed connection failed")
                if stop_event.is_set():
                    break
                logger.warning("Disconnected; reconnecting in %.1fs", backoff)
                stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, args.max_backoff)
        finally:
            pipeline = state.get("pipeline")
            metrics = pipeline.finalize() if pipeline is not None else None
            session.close()
            sink.close()

    if session.directory is not None:
        logger.info("Raw recording saved to %s", session.directory)
    if metrics is None:
        logger.warning("No session was ever identified — nothing ingested")
        return 1
    print(metrics.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())


def _cmd_archive_positions(args: argparse.Namespace) -> int:
    import signal
    import threading
    from datetime import UTC, datetime, timedelta
    from types import FrameType

    from f1_pipeline.archive import (
        archive_positions_once,
        isolated_loader,
        store_sessions,
        watch,
    )

    # FastF1 runs only in a child process per read, never with its cache:
    # see f1_pipeline.archive's module docstring.
    uncached_positions = isolated_loader(args.fastf1_cache)

    settle = timedelta(minutes=args.settle_minutes)
    max_age = timedelta(days=args.max_age_days)

    if args.watch is not None:
        stop = threading.Event()

        def handle_signal(signum: int, _frame: FrameType | None) -> None:
            logger.info("Received signal %s; stopping after this pass", signum)
            stop.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        logger.info("Watching for finished sessions every %.0fs", args.watch)
        watch(
            lambda: connect(args.dsn),
            args.historical_root,
            interval=timedelta(seconds=args.watch),
            loader=uncached_positions,
            stop=stop,
            settle=settle,
            max_age=max_age,
        )
        return 0

    with connect(args.dsn) as conn:
        sessions = store_sessions(conn)
    report = archive_positions_once(
        sessions,
        args.historical_root,
        now=datetime.now(UTC),
        loader=uncached_positions,
        settle=settle,
        max_age=max_age,
    )
    for key, rows in report.written:
        logger.info("archived %s: %d samples", key, rows)
    for key, reason in report.pending:
        logger.info("pending %s: %s", key, reason)
    return 0
