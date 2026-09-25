"""Measured car positions for live-ingested sessions, from F1's archive.

The live feed only sends `Position.z` to an F1TV-authenticated client, and
the worker runs unauthenticated (`f1-pipeline live --no-auth`), so every
session it ingests has no car positions. F1 publishes them after the
session in its static archive, which FastF1 reads. `archive-positions`
closes the gap: for each store session that has finished and has no
positions yet, it imports them (`f1_backfill.positions`) into the
historical tree, filed by session key, which is where a replay looks
(`f1_kernel.HistoricalSource.position_track`). The replay then puts them on
the session's own clock (`f1_kernel.positions.session_clock_offset`).

Run once, or with `--watch` as a long-lived service that checks every few
minutes. A session is only tried once it has been quiet for `settle` (the
archive is written after the session), and an import is only kept if it
reaches the session's last logged lap, so a partial archive is retried
instead of being filed as complete. The CLI reads the archive with
FastF1's cache DISABLED: FastF1 pickles whatever it downloads with no
completeness check, so a cached partial read would be served back on every
retry. Retries back off (`RetrySchedule`), so an archive that never
completes costs a handful of downloads, not one every pass for a month.
Each read runs in a short-lived child process (`isolated_loader`): FastF1
peaks around half a gigabyte, and a long-lived service would otherwise
hold that for the week until the next race.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import TupleRow

from f1_domain import SessionKey, SessionType
from f1_domain import session_type_from_label as session_type_from_label
from f1_historical_adapter.reader import session_directory

logger = logging.getLogger(__name__)

POSITIONS_FILE = "car_positions.parquet"

DEFAULT_SETTLE = timedelta(minutes=90)
"""How long a session must have been quiet before its archive is read."""

DEFAULT_MAX_AGE = timedelta(days=30)
"""Sessions older than this are no longer retried: the archive either had
them long ago or never will (a test session, a feed outage)."""

RETRY_BASE = timedelta(minutes=15)
RETRY_CAP = timedelta(hours=12)
MAX_ATTEMPTS = 8
"""With `RETRY_BASE` doubling up to `RETRY_CAP`: about 40 hours of tries
after the session settles, then the watcher gives up on it until it is
restarted (or the import is run by hand)."""

COMPLETE_WITHIN = timedelta(minutes=5)
"""An import must reach this close to the session's last logged lap to
count as complete."""

_FASTF1_SESSION_NAMES: dict[SessionType, str] = {
    SessionType.PRACTICE_1: "Practice 1",
    SessionType.PRACTICE_2: "Practice 2",
    SessionType.PRACTICE_3: "Practice 3",
    SessionType.QUALIFYING: "Qualifying",
    SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    SessionType.SPRINT: "Sprint",
    SessionType.RACE: "Race",
}

PositionLoader = Callable[[int, int, str], Mapping[str, pd.DataFrame]]


@dataclass(frozen=True, slots=True)
class StoreSession:
    key: SessionKey
    last_event_utc: datetime
    last_lap_utc: datetime | None


@dataclass
class ArchiveReport:
    written: list[tuple[SessionKey, int]] = field(default_factory=list[tuple[SessionKey, int]])
    pending: list[tuple[SessionKey, str]] = field(default_factory=list[tuple[SessionKey, str]])


class RetrySchedule:
    """When each pending session may be tried again: `RETRY_BASE` after the
    first failure, doubling to `RETRY_CAP`, never after `MAX_ATTEMPTS`."""

    def __init__(self) -> None:
        self._attempts: dict[SessionKey, tuple[int, datetime]] = {}

    def waiting(self, now: datetime) -> set[SessionKey]:
        """Sessions not to try on a pass at `now`."""
        return {
            key
            for key, (attempts, retry_at) in self._attempts.items()
            if attempts >= MAX_ATTEMPTS or retry_at > now
        }

    def failed(self, key: SessionKey, now: datetime) -> int:
        """Record one more failed attempt; returns how many so far."""
        attempts = self._attempts.get(key, (0, now))[0] + 1
        delay = min(RETRY_CAP, RETRY_BASE * 2 ** (attempts - 1))
        self._attempts[key] = (attempts, now + delay)
        return attempts

    def succeeded(self, key: SessionKey) -> None:
        self._attempts.pop(key, None)


def store_sessions(conn: psycopg.Connection[TupleRow]) -> list[StoreSession]:
    """Every session in the event log, with when it last logged anything
    and when it last logged a lap."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT session_year, session_round, session_type, max(ts_utc), "
            "max(ts_utc) FILTER (WHERE kind = 'lap_completed') "
            "FROM events GROUP BY session_year, session_round, session_type"
        )
        rows = cur.fetchall()
    return [
        StoreSession(
            key=SessionKey(
                year=int(year),
                round=int(round_no),
                session_type=session_type_from_label(str(session_type)),
            ),
            last_event_utc=last_event,
            last_lap_utc=last_lap,
        )
        for year, round_no, session_type, last_event, last_lap in rows
    ]


def sessions_needing_positions(
    sessions: list[StoreSession],
    historical_root: Path,
    *,
    now: datetime,
    settle: timedelta = DEFAULT_SETTLE,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> list[StoreSession]:
    """The finished, recent sessions with no positions on file."""
    return [
        session
        for session in sessions
        if now - max_age <= session.last_event_utc <= now - settle
        and not (session_directory(historical_root, session.key) / POSITIONS_FILE).is_file()
    ]


def archive_positions_once(
    sessions: list[StoreSession],
    historical_root: Path,
    *,
    now: datetime,
    loader: PositionLoader,
    settle: timedelta = DEFAULT_SETTLE,
    max_age: timedelta = DEFAULT_MAX_AGE,
    skip: Collection[SessionKey] = (),
) -> ArchiveReport:
    """One pass: import every session that needs it (other than `skip`),
    keeping only complete imports. Never raises for one session's failure,
    not even FastF1's own `exit()` on a download that returned nothing."""
    from f1_backfill.positions import position_table

    report = ArchiveReport()
    for session in sessions_needing_positions(
        sessions, historical_root, now=now, settle=settle, max_age=max_age
    ):
        key = session.key
        if key in skip:
            continue
        name = _FASTF1_SESSION_NAMES.get(key.session_type)
        if name is None:
            report.pending.append((key, "no FastF1 name for this session type"))
            continue
        try:
            table = position_table(loader(key.year, key.round, name))
        except (Exception, SystemExit) as exc:
            logger.warning("positions for %s not available yet: %s", key, exc)
            report.pending.append((key, str(exc)))
            continue
        if table.empty:
            report.pending.append((key, "the archive has no positions yet"))
            continue
        last_sample = _utc(table["Date"].max())
        if (
            session.last_lap_utc is not None
            and last_sample < session.last_lap_utc - COMPLETE_WITHIN
        ):
            report.pending.append(
                (key, f"archive ends at {last_sample:%H:%M:%S}, before the last lap; partial")
            )
            continue
        target = session_directory(historical_root, key) / POSITIONS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".parquet.tmp")
        table.to_parquet(partial, engine="pyarrow", index=False, compression="zstd")
        partial.replace(target)
        logger.info("%s: %d position samples archived", key, len(table))
        report.written.append((key, len(table)))
    return report


def watch(
    connect: Callable[[], psycopg.Connection[TupleRow]],
    historical_root: Path,
    *,
    interval: timedelta,
    loader: PositionLoader,
    stop: threading.Event,
    settle: timedelta = DEFAULT_SETTLE,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> None:
    """Pass after pass until `stop` is set. A connection per pass, so a
    database restart between passes costs one pass, not the service."""
    retries = RetrySchedule()
    while not stop.is_set():
        try:
            with connect() as conn:
                sessions = store_sessions(conn)
            now = datetime.now(UTC)
            report = archive_positions_once(
                sessions,
                historical_root,
                now=now,
                loader=loader,
                settle=settle,
                max_age=max_age,
                skip=retries.waiting(now),
            )
            for key, _ in report.written:
                retries.succeeded(key)
            for key, reason in report.pending:
                attempts = retries.failed(key, now)
                if attempts >= MAX_ATTEMPTS:
                    logger.warning("%s: giving up on positions (%s)", key, reason)
                else:
                    logger.info("%s: positions pending, attempt %d (%s)", key, attempts, reason)
        except Exception:
            logger.exception("archive-positions pass failed; retrying next pass")
        stop.wait(timeout=interval.total_seconds())


def load_uncached_positions(
    year: int, round_no: int, name: str, fastf1_cache: Path
) -> dict[str, pd.DataFrame]:
    """One session's positions from F1's archive, bypassing FastF1's cache
    (see the module docstring for why)."""
    import fastf1

    from f1_backfill.fastf1_source import configure_cache
    from f1_backfill.positions import load_position_data

    configure_cache(fastf1_cache)
    with fastf1.Cache.disabled():
        return dict(load_position_data(year, round_no, name))


def isolated_loader(fastf1_cache: Path) -> PositionLoader:
    """`load_uncached_positions` in a fresh child process per call, so its
    memory is returned when the call ends. A child that dies (FastF1 calls
    `exit()` on a failed download) surfaces as an exception here, which
    `archive_positions_once` records as pending."""

    def load(year: int, round_no: int, name: str) -> Mapping[str, pd.DataFrame]:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
            return pool.submit(load_uncached_positions, year, round_no, name, fastf1_cache).result()

    return load


def _utc(value: Any) -> datetime:
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)
    return stamp.to_pydatetime()


__all__ = [
    "DEFAULT_MAX_AGE",
    "DEFAULT_SETTLE",
    "MAX_ATTEMPTS",
    "ArchiveReport",
    "RetrySchedule",
    "StoreSession",
    "archive_positions_once",
    "isolated_loader",
    "load_uncached_positions",
    "sessions_needing_positions",
    "store_sessions",
    "watch",
]
