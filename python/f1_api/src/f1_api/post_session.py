"""`f1-post-session-check`: after every live session, what viewers were
shown, checked against F1's own archive (ROADMAP W0, "Post-session data
check").

Runs as the `postsession` compose service (`--watch`). For each session in
the store's event log that has been quiet for `settle` (F1 publishes its
archive after a session) and has no report yet:

1. The session's archive tables are filed in the backfill unless they are
   there already: read from F1's archive through FastF1 in a short-lived
   child process with FastF1's cache off (`f1_pipeline.archive` explains
   both), kept only when complete (a classification, and laps reaching the
   last lap viewers saw), then catalogued like any backfilled session, so
   the standings and circuit pages count it too.
2. The store's events, what viewers saw, are compared with those tables
   (`f1_historical_adapter.compare_event_streams`).
3. The report goes to `<data>/post_session/<key>.md` (and `.json`).
   Anything beyond `Tolerances` is logged at ERROR, one line each, in
   `docker compose logs postsession`. Logs only, by choice: no email.

A session with a report is never checked again; `--session KEY` checks one
now and replaces its report. A session whose archive is not there yet is
retried with backoff while it is younger than `max_age`.

Found 2026-10-12, by hand, what this now does on its own: in the
red-flagged Bahrain race the live tower showed the wrong tyre on 32 laps.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import signal
import threading
from collections.abc import Callable, Collection
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import pandas as pd
import psycopg

from f1_api.config import get_settings
from f1_domain import (
    DomainEvent,
    LapCompleted,
    SessionKey,
    SessionType,
    session_type_from_label,
)
from f1_historical_adapter import ComparisonReport, compare_event_streams, events_from_parquet

_log = logging.getLogger("f1_api.post_session")

DEFAULT_SETTLE = timedelta(hours=2)
"""How long a session must have been quiet before its archive is read."""

DEFAULT_MAX_AGE = timedelta(days=7)
"""Older sessions are not tried: the archive had them long ago or never will."""

RETRY_BASE = timedelta(minutes=30)
RETRY_CAP = timedelta(hours=6)

COMPARED_KINDS = ("lap_completed", "stint_started", "pit_entry", "position_changed")

FASTF1_NAMES: dict[SessionType, str] = {
    SessionType.PRACTICE_1: "Practice 1",
    SessionType.PRACTICE_2: "Practice 2",
    SessionType.PRACTICE_3: "Practice 3",
    SessionType.QUALIFYING: "Qualifying",
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT: "Sprint",
    SessionType.RACE: "Race",
}

_RACES = {SessionType.RACE, SessionType.SPRINT}


@dataclass(frozen=True, slots=True)
class Tolerances:
    """How much disagreement a session may show before it is reported.
    Shares of the laps compared (or, for `missed_laps`, of the archive's
    timed laps). Per-driver fields (laps completed, stops, final order)
    allow none."""

    missed_laps: float = 0.02
    lap_times: float = 0.01
    compound: float = 0.02
    tyre_age: float = 0.05


DEFAULT_TOLERANCES = Tolerances()


def findings(report: ComparisonReport, tolerances: Tolerances = DEFAULT_TOLERANCES) -> list[str]:
    """What in `report` is worth telling someone, one sentence each."""
    by_name = {agreement.name: agreement for agreement in report.agreements}
    found: list[str] = []
    session_type = report.session_key.session_type

    times = by_name["lap_times"]
    archive_laps = times.compared + times.only_historical
    if archive_laps and times.only_historical / archive_laps > tolerances.missed_laps:
        found.append(
            f"Live missed {times.only_historical} of the archive's {archive_laps} timed laps."
        )
    # A live time the archive lacks is not reported: F1's archive leaves laps
    # untimed that the feed timed (out-laps, red-flag laps).
    per_lap = [
        ("lap_times", "lap times", tolerances.lap_times),
        ("compound", "tyre compounds", tolerances.compound),
    ]
    if session_type in _RACES:
        # Strategy rests on tyre age in a race. Outside one it is reported,
        # not alerted on: the feed's count at a run's first lap is a couple
        # of laps off the archive's (2026 Singapore sprint qualifying).
        per_lap.append(("tyre_age", "tyre ages", tolerances.tyre_age))
    for name, label, limit in per_lap:
        agreement = by_name[name]
        if agreement.compared and agreement.mismatched / agreement.compared > limit:
            found.append(
                f"{agreement.mismatched} of {agreement.compared} laps show other {label} "
                f"than the archive: {'; '.join(agreement.examples)}."
            )
    per_driver: tuple[tuple[str, str], ...] = ()
    if session_type in _RACES:
        per_driver = (
            ("laps_completed", "laps completed"),
            ("stops", "stops"),
            ("final_positions", "final places (post-race penalties also do this)"),
        )
    # Qualifying's and practice's final order is in the report, not alerted
    # on: deleted times and penalties reorder it after the session.
    for name, label in per_driver:
        agreement = by_name[name]
        if agreement.mismatched:
            found.append(
                f"{agreement.mismatched} drivers show other {label} than the archive: "
                f"{'; '.join(agreement.examples)}."
            )
    return found


# ---------------------------------------------------------------------------
# F1's archive into the backfill
# ---------------------------------------------------------------------------

ArchiveFetcher = Callable[[SessionKey], tuple[Any, dict[str, pd.DataFrame]]]
"""A session's backfill key and archive tables (`fetch_archive`)."""


def fetch_archive(
    year: int, round_no: int, name: str, fastf1_cache: Path
) -> tuple[Any, dict[str, pd.DataFrame]]:
    """One session read from F1's archive, bypassing FastF1's cache: FastF1
    pickles whatever it downloads with no completeness check, so a cached
    partial read would come back on every retry."""
    import fastf1

    from f1_backfill.fastf1_source import configure_cache, extract_tables, load_session
    from f1_backfill.schedule import sessions_for_season

    configure_cache(fastf1_cache)
    with fastf1.Cache.disabled():
        key = next(
            (k for k in sessions_for_season(year, round_filter=round_no) if k.session_type == name),
            None,
        )
        if key is None:
            raise LookupError(f"{name} is not on the {year} round {round_no} schedule")
        return key, extract_tables(load_session(year, round_no, name))


def isolated_fetcher(fastf1_cache: Path) -> ArchiveFetcher:
    """`fetch_archive` in a fresh child process per call: FastF1 peaks around
    a gigabyte, which a service idle all week should not keep."""

    def fetch(session_key: SessionKey) -> tuple[Any, dict[str, pd.DataFrame]]:
        name = FASTF1_NAMES[session_key.session_type]
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
            return pool.submit(
                fetch_archive, session_key.year, session_key.round, name, fastf1_cache
            ).result()

    return fetch


def _catalog_key(key: SessionKey) -> str | None:
    from f1_backfill.schedule import SessionKey as BackfillKey

    name = FASTF1_NAMES.get(key.session_type)
    if name is None:
        return None
    return BackfillKey(key.year, key.round, "", name, "").catalog_key


def is_filed(historical_root: Path, key: SessionKey) -> bool:
    """Whether the backfill catalog already holds this session."""
    from f1_backfill.catalog import CATALOG_FILENAME, Catalog

    catalog_key = _catalog_key(key)
    catalog = Catalog.load(historical_root / CATALOG_FILENAME)
    return catalog_key is not None and catalog_key in catalog.sessions


def incomplete(tables: dict[str, pd.DataFrame], last_lap: int | None) -> str | None:
    """Why an archive read is not the whole session yet, or None."""
    laps = tables.get("laps")
    results = tables.get("results")
    if laps is None or laps.empty:
        return "the archive has no laps yet"
    if results is None or results.empty:
        return "the archive has no classification yet"
    if last_lap is not None:
        reached = int(pd.to_numeric(laps["LapNumber"], errors="coerce").max())
        if reached < last_lap:
            return f"the archive reaches lap {reached}, viewers saw lap {last_lap}"
    return None


def file_archive(
    key: SessionKey,
    historical_root: Path,
    fetch: ArchiveFetcher,
    *,
    last_lap: int | None,
    now: datetime,
    openf1: Any = None,
) -> str | None:
    """Put the session's archive tables in the backfill. None once they are
    there; otherwise why not yet."""
    from f1_backfill.catalog import CATALOG_FILENAME, Catalog
    from f1_backfill.openf1 import OpenF1Client
    from f1_backfill.pipeline import file_session

    if is_filed(historical_root, key):
        return None
    if key.session_type not in FASTF1_NAMES:
        return "no archive for this session type"
    try:
        backfill_key, tables = fetch(key)
    except (Exception, SystemExit) as exc:
        return f"the archive cannot be read yet: {exc}"
    problem = incomplete(tables, last_lap)
    if problem is not None:
        return problem
    catalog_path = historical_root / CATALOG_FILENAME
    file_session(
        backfill_key,
        tables,
        historical_root,
        openf1 if openf1 is not None else OpenF1Client(),
        Catalog.load(catalog_path),
        catalog_path,
        lambda: now,
    )
    _log.info("%s: archive filed in the backfill", key)
    return None


# ---------------------------------------------------------------------------
# one pass
# ---------------------------------------------------------------------------


def report_path(report_dir: Path, key: SessionKey) -> Path:
    return report_dir / f"{key.year}-{key.round}-{key.session_type.value}.md"


def write_report(
    report_dir: Path, report: ComparisonReport, found: list[str], now: datetime
) -> Path:
    key = report.session_key
    path = report_path(report_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = (
        "Everything viewers saw matches F1's archive within tolerance."
        if not found
        else "\n".join(f"- {line}" for line in found)
    )
    path.write_text(
        f"# Post-session check: {key.year} round {key.round} {key.session_type.value}\n\n"
        f"Checked {now:%Y-%m-%d %H:%M} UTC: the live store against F1's archive.\n\n"
        f"{verdict}\n\n```\n{report.render()}\n```\n",
        encoding="utf-8",
    )
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "session": f"{key.year}-{key.round}-{key.session_type.value}",
                "checked_at": now.isoformat(),
                "findings": found,
                "agreements": [asdict(agreement) for agreement in report.agreements],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


@dataclass
class PassResult:
    checked: list[tuple[SessionKey, list[str]]] = field(
        default_factory=list[tuple[SessionKey, list[str]]]
    )
    pending: list[tuple[SessionKey, str]] = field(default_factory=list[tuple[SessionKey, str]])


def _last_lap(events: list[DomainEvent]) -> int | None:
    laps = [e.lap_number for e in events if isinstance(e, LapCompleted) and e.lap_number]
    return max(laps, default=None)


def check_session(
    key: SessionKey,
    live: list[DomainEvent],
    historical_root: Path,
    report_dir: Path,
    fetch: ArchiveFetcher,
    *,
    now: datetime,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
    openf1: Any = None,
) -> list[str] | str:
    """File, compare, report and log one session. The findings, or (a
    string) why the archive is not there yet."""
    reason = file_archive(
        key, historical_root, fetch, last_lap=_last_lap(live), now=now, openf1=openf1
    )
    if reason is not None:
        return reason
    archive = [e for e in events_from_parquet(historical_root, key) if e.kind in COMPARED_KINDS]
    report = compare_event_streams(key, live, archive)
    found = findings(report, tolerances)
    path = write_report(report_dir, report, found, now)
    name = f"{key.year} round {key.round} {key.session_type.value}"
    if found:
        _log.error("post-session check %s: %d differences (%s)", name, len(found), path)
        for line in found:
            _log.error("post-session check %s: %s", name, line)
    else:
        _log.info("post-session check %s: matches the archive (%s)", name, path)
    return found


class Backoff:
    """When a session whose archive was not ready may be tried again:
    `RETRY_BASE` after the first miss, doubling to `RETRY_CAP`."""

    def __init__(self) -> None:
        self._next: dict[SessionKey, tuple[int, datetime]] = {}

    def waiting(self, now: datetime) -> set[SessionKey]:
        return {key for key, (_, at) in self._next.items() if at > now}

    def missed(self, key: SessionKey, now: datetime) -> None:
        attempts = self._next.get(key, (0, now))[0] + 1
        self._next[key] = (attempts, now + min(RETRY_CAP, RETRY_BASE * 2 ** (attempts - 1)))

    def done(self, key: SessionKey) -> None:
        self._next.pop(key, None)


def check_once(
    list_sessions: Callable[[], list[tuple[SessionKey, datetime]]],
    load_live: Callable[[SessionKey], list[DomainEvent]],
    historical_root: Path,
    report_dir: Path,
    fetch: ArchiveFetcher,
    *,
    now: datetime,
    settle: timedelta = DEFAULT_SETTLE,
    max_age: timedelta = DEFAULT_MAX_AGE,
    skip: Collection[SessionKey] = (),
    openf1: Any = None,
) -> PassResult:
    """One pass over the finished, unreported sessions (other than `skip`).
    One session's failure never stops the pass."""
    result = PassResult()
    for key, last_event in list_sessions():
        if not now - max_age <= last_event <= now - settle:
            continue
        if key in skip or report_path(report_dir, key).exists():
            continue
        try:
            outcome = check_session(
                key,
                load_live(key),
                historical_root,
                report_dir,
                fetch,
                now=now,
                openf1=openf1,
            )
        except Exception as exc:
            _log.exception("post-session check of %s failed", key)
            result.pending.append((key, f"check failed: {exc}"))
            continue
        if isinstance(outcome, str):
            result.pending.append((key, outcome))
        else:
            result.checked.append((key, outcome))
    return result


# ---------------------------------------------------------------------------
# the store, and the command
# ---------------------------------------------------------------------------


def store_sessions(dsn: str) -> list[tuple[SessionKey, datetime]]:
    """Every session in the event log, with when it last logged anything."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT session_year, session_round, session_type, max(ts_utc) "
            "FROM events GROUP BY session_year, session_round, session_type"
        )
        rows = cur.fetchall()
    return [
        (
            SessionKey(
                year=int(year),
                round=int(round_no),
                session_type=session_type_from_label(str(session_type)),
            ),
            last_event,
        )
        for year, round_no, session_type, last_event in rows
    ]


def store_events(dsn: str, key: SessionKey) -> list[DomainEvent]:
    """The events viewers were sent for `key`, the compared kinds only."""
    from f1_store import replay_events

    with psycopg.connect(dsn) as conn:
        return list(replay_events(conn, key, kinds=COMPARED_KINDS))


def _session_key(text: str) -> SessionKey:
    year, round_no, session_type = text.split("-", 2)
    return SessionKey(year=int(year), round=int(round_no), session_type=SessionType(session_type))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="After every live session, check what viewers saw against F1's archive."
    )
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="a pass every SECONDS")
    parser.add_argument("--session", help="check this one now (e.g. 2026-17-race)")
    parser.add_argument("--settle-minutes", type=float, default=DEFAULT_SETTLE.total_seconds() / 60)
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE.days)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    settings = get_settings()
    dsn = settings.store_dsn
    if not dsn:
        parser.error("no store configured (F1_STORE_DSN)")
    historical_root = settings.historical_root
    report_dir = args.report_dir or historical_root.parent / "post_session"
    fetch = isolated_fetcher(settings.fastf1_cache_dir)

    if args.session:
        key = _session_key(args.session)
        outcome = check_session(
            key,
            store_events(dsn, key),
            historical_root,
            report_dir,
            fetch,
            now=datetime.now(UTC),
        )
        if isinstance(outcome, str):
            _log.warning("%s: not checked: %s", args.session, outcome)
            return 1
        return 1 if outcome else 0

    settle = timedelta(minutes=args.settle_minutes)
    max_age = timedelta(days=args.max_age_days)
    stop = threading.Event()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        _log.info("signal %s: stopping after this pass", signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    backoff = Backoff()
    while not stop.is_set():
        now = datetime.now(UTC)
        try:
            result = check_once(
                lambda: store_sessions(dsn),
                lambda key: store_events(dsn, key),
                historical_root,
                report_dir,
                fetch,
                now=now,
                settle=settle,
                max_age=max_age,
                skip=backoff.waiting(now),
            )
            for key, _ in result.checked:
                backoff.done(key)
            for key, reason in result.pending:
                backoff.missed(key, now)
                _log.info("%s: not checked yet (%s)", key, reason)
        except Exception:
            _log.exception("post-session pass failed; retrying next pass")
        if args.watch is None:
            break
        stop.wait(timeout=args.watch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
