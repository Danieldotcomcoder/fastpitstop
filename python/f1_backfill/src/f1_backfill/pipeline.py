"""Orchestrates one `--season` backfill run: schedule -> FastF1 + OpenF1 ->
partitioned parquet -> catalog.

Testing note: FastF1's own HTTP/caching layer is not mocked here — it's
internal to the `fastf1` library (see its own test suite for that).
Instead, `session_loader` is dependency-injected, so pipeline tests supply
a fake loader returning a `fastf1_source.SessionLike` built from small
in-memory DataFrames — no network, no fastf1 internals involved. OpenF1 IS
a real HTTP layer this project owns (`f1_backfill.openf1`), so it get to be
mocked at the HTTP boundary in that module's own tests; here it's still
injected as an `OpenF1Client` for the same "no network in unit tests"
reason.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from f1_backfill.catalog import CATALOG_FILENAME, Catalog
from f1_backfill.fastf1_source import SessionLike, extract_tables, load_session
from f1_backfill.openf1 import OpenF1Client
from f1_backfill.schedule import (
    ScheduleProvider,
    SessionKey,
    default_schedule_provider,
    sessions_for_season,
)
from f1_backfill.writer import write_tables

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = Path("data/historical")

SessionLoader = Callable[[int, int, str], SessionLike]


@dataclass
class BackfillReport:
    acquired: list[SessionKey] = field(default_factory=list[SessionKey])
    skipped: list[SessionKey] = field(default_factory=list[SessionKey])
    failed: list[tuple[SessionKey, str]] = field(default_factory=list[tuple[SessionKey, str]])


def run_backfill(
    season: int,
    *,
    round_filter: int | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    catalog_path: Path | None = None,
    refresh: bool = False,
    schedule_provider: ScheduleProvider = default_schedule_provider,
    session_loader: SessionLoader = load_session,
    openf1_client: OpenF1Client | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackfillReport:
    """Acquire every session for `season` (optionally just `round_filter`)
    that isn't already in the catalog, writing partitioned parquet and
    updating the catalog after each session so a crash mid-run leaves
    already-acquired sessions durably recorded (each session is one
    self-contained unit of work, not the whole run).
    """
    resolved_catalog_path = catalog_path if catalog_path is not None else out_dir / CATALOG_FILENAME
    catalog = Catalog.load(resolved_catalog_path)
    client = openf1_client if openf1_client is not None else OpenF1Client()

    report = BackfillReport()
    for key in sessions_for_season(
        season, round_filter=round_filter, schedule_provider=schedule_provider
    ):
        if not refresh and catalog.has(key):
            report.skipped.append(key)
            continue
        try:
            _acquire_one(key, out_dir, session_loader, client, catalog, resolved_catalog_path, now)
            report.acquired.append(key)
        except Exception as exc:  # one bad session must not abort the whole run
            logger.exception("Failed to acquire %s", key.catalog_key)
            report.failed.append((key, str(exc)))
    return report


def _acquire_one(
    key: SessionKey,
    out_dir: Path,
    session_loader: SessionLoader,
    client: OpenF1Client,
    catalog: Catalog,
    catalog_path: Path,
    now: Callable[[], datetime],
) -> None:
    session = session_loader(key.season, key.round, key.session_type)
    tables = extract_tables(session)
    sources = ["fastf1"]

    openf1_session_key = client.find_session_key(
        year=key.season, location=key.location, session_name=key.session_type
    )
    if openf1_session_key is not None:
        tables["intervals"] = _intervals_frame(client.get_intervals(openf1_session_key))
        tables["race_control"] = pd.DataFrame(client.get_race_control(openf1_session_key))
        tables["team_radio"] = pd.DataFrame(client.get_team_radio(openf1_session_key))
        sources.append("openf1")
    else:
        logger.warning(
            "No OpenF1 session matched %s (%s, %s) - fastf1-only tables for this session",
            key.catalog_key,
            key.location,
            key.session_type,
        )

    row_counts = write_tables(out_dir, key, tables)
    catalog.record(key, sources=sources, tables=row_counts, acquired_at=now())
    catalog.save(catalog_path)


def _intervals_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Build an intervals DataFrame safe for parquet.

    OpenF1 mixes floats and strings like '+1 LAP' in gap_to_leader/interval;
    casting those columns to string avoids pyarrow type-inference failures.
    """
    df = pd.DataFrame(rows)
    for col in ("gap_to_leader", "interval"):
        if col in df.columns:
            df[col] = df[col].astype("string")
    return df
