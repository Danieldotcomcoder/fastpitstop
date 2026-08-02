"""`f1-pipeline`: the Phase 1 entry point, wiring each source to Postgres.

    f1-pipeline replay <recording> --round N        # f1_replay -> pipeline -> DB
    f1-pipeline ingest-historical --season Y         # adapter  -> pipeline -> DB
    f1-pipeline live --year Y --round N --session-type race   # feed -> pipeline -> DB

All three converge on the SAME pipeline (`run`/`run_events`). The DB target
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
from f1_recording import RecordingReader
from f1_store import PostgresSink, apply_migrations
from f1_store.config import connect

logger = logging.getLogger("f1_pipeline")

DEFAULT_HISTORICAL_ROOT = Path("data/historical")
DEFAULT_RECORDINGS_OUT = Path("data/recordings")
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
    live.add_argument("--year", type=int, required=True)
    live.add_argument("--round", type=int, required=True)
    live.add_argument("--session-type", default="race", choices=_SESSION_TYPE_CHOICES)
    live.add_argument("--out", type=Path, default=DEFAULT_RECORDINGS_OUT, help="raw recording dir")
    live.add_argument("--session-name", default=None)
    live.add_argument("--no-auth", action="store_true")
    live.add_argument("--idle-timeout", type=float, default=60.0)
    live.add_argument("--max-backoff", type=float, default=60.0)
    live.set_defaults(func=_cmd_live)

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

    key = session_key(year=args.year, round=args.round, session_type=args.session_type)
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
        pipeline = Pipeline(key, sink)
        session = RecordingSession(
            args.out, topics=topics, recorder_version=__version__, session_name=args.session_name
        )

        def on_subscribe(snapshot: dict[str, Any]) -> None:
            session.on_subscribe_result(snapshot)  # persist raw (+ reconnect markers)
            pipeline.seed(snapshot)  # seed the normalizer (silent)

        def on_feed(topic: str, payload: Any) -> None:
            pipeline.process(session.on_feed_message(topic, payload))

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
            metrics = pipeline.finalize()
            session.close()
            sink.close()

    if session.directory is not None:
        logger.info("Raw recording saved to %s", session.directory)
    print(metrics.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
