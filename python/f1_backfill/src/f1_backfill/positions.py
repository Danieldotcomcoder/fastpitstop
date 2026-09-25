"""Measured car positions: F1's own `Position.z` channel as a clean table.

Since 2025 the live SignalR feed only sends `Position.z` to an F1TV-
authenticated client; ours is not one, so a live session carries no car
positions at all (the 2026 recordings subscribe to the topic and receive
nothing). F1 still publishes the same channel to its public static archive
for every finished session, and FastF1 downloads it whenever a session is
loaded with `telemetry=True`. This module turns it into the
`car_positions` table: one row per (car, sample), ~4 Hz per car, in the
feed's own track-local units (~1/10 m). Those are the units every circuit
outline is derived in (`f1_kernel.track_outline`), so a sample plots on the
map with no transform.

Two ways in, one table:

- `position_table` normalizes an already-loaded session's `pos_data`;
  `fastf1_source.extract_tables` calls it, so every new backfill carries
  positions.
- `run_positions` (`f1-backfill --positions-only`) writes the table for
  sessions acquired before it existed, reading FastF1's on-disk cache, so
  nothing already cached is downloaded again. It can also target a session
  the catalog does not list (`include_uncatalogued`): a live-ingested one,
  served from the store, whose positions only exist in the archive.

Rows at exactly (0, 0, 0) are dropped. That is the feed's "no fix yet"
placeholder while a car sits in the garage, not a place on the circuit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pandas as pd

from f1_backfill.catalog import CATALOG_FILENAME, Catalog
from f1_backfill.schedule import (
    ScheduleProvider,
    SessionKey,
    default_schedule_provider,
    sessions_for_season,
    slugify,
)
from f1_backfill.writer import session_dir

logger = logging.getLogger(__name__)

POSITIONS_TABLE = "car_positions"
POSITION_COLUMNS = ("DriverNumber", "Date", "X", "Y", "Z", "Status")

PositionLoader = Callable[[int, int, str], Mapping[str, pd.DataFrame]]
"""(season, round, session name) -> FastF1 `pos_data` (driver -> samples)."""


def empty_position_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "DriverNumber": pd.Series(dtype="string"),
            "Date": pd.Series(dtype="datetime64[ns]"),
            "X": pd.Series(dtype="int32"),
            "Y": pd.Series(dtype="int32"),
            "Z": pd.Series(dtype="int32"),
            "Status": pd.Series(dtype="string"),
        }
    )


def position_table(pos_data: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Every driver's samples in one table, sorted by (Date, DriverNumber).

    `Date` stays tz-naive UTC, as FastF1 gives it and as every other table
    here stores its timestamps (DATA_CONTRACTS.md)."""
    frames: list[pd.DataFrame] = []
    for driver, samples in pos_data.items():
        frame = pd.DataFrame(samples)
        if frame.empty or not {"Date", "X", "Y"}.issubset(frame.columns):
            continue
        z = frame["Z"] if "Z" in frame.columns else pd.Series(0, index=frame.index)
        status = (
            frame["Status"] if "Status" in frame.columns else pd.Series(None, index=frame.index)
        )
        frames.append(
            pd.DataFrame(
                {
                    "DriverNumber": str(driver),
                    "Date": pd.to_datetime(frame["Date"]),
                    "X": pd.to_numeric(frame["X"], errors="coerce"),
                    "Y": pd.to_numeric(frame["Y"], errors="coerce"),
                    "Z": pd.to_numeric(z, errors="coerce").fillna(0),
                    "Status": status,
                }
            )
        )
    if not frames:
        return empty_position_table()

    table = pd.concat(frames, ignore_index=True)
    table = table.dropna(subset=["Date", "X", "Y"])
    garage = (table["X"] == 0) & (table["Y"] == 0) & (table["Z"] == 0)
    table = table.loc[~garage]
    table = table.astype(
        {"DriverNumber": "string", "X": "int32", "Y": "int32", "Z": "int32", "Status": "string"}
    )
    table = table.sort_values(["Date", "DriverNumber"], kind="stable").reset_index(drop=True)
    return table.loc[:, list(POSITION_COLUMNS)]


def load_position_data(season: int, round_no: int, session_type: str) -> Mapping[str, pd.DataFrame]:
    """One session's `pos_data` via FastF1's public API.

    `telemetry=True` also loads car telemetry (FastF1 aligns the two
    channels' clocks together), but from the cache that is a read, not a
    download. Laps, weather and messages are skipped: nothing here needs
    them. Returns `{}` when FastF1 has no positions for the session."""
    import fastf1

    session: Any = fastf1.get_session(season, round_no, session_type)
    session.load(laps=False, telemetry=True, weather=False, messages=False)
    try:
        return cast(Mapping[str, pd.DataFrame], session.pos_data)
    except Exception:  # FastF1's DataNotLoadedError: the archive had none
        return {}


@dataclass
class PositionsReport:
    written: list[tuple[SessionKey, int]] = field(default_factory=list[tuple[SessionKey, int]])
    skipped: list[SessionKey] = field(default_factory=list[SessionKey])
    failed: list[tuple[SessionKey, str]] = field(default_factory=list[tuple[SessionKey, str]])


def run_positions(
    season: int,
    *,
    round_filter: int | None = None,
    session_filter: str | None = None,
    out_dir: Path,
    catalog_path: Path | None = None,
    refresh: bool = False,
    include_uncatalogued: bool = False,
    schedule_provider: ScheduleProvider = default_schedule_provider,
    loader: PositionLoader = load_position_data,
) -> PositionsReport:
    """Write `car_positions.parquet` for each matching session.

    A session is skipped when its table already exists (unless `refresh`),
    and when the catalog does not list it (unless `include_uncatalogued`),
    so a plain run only touches sessions that were really backfilled. The
    catalog's row count for the table is updated after each session, so an
    interrupted run keeps what it wrote."""
    resolved_catalog = catalog_path if catalog_path is not None else out_dir / CATALOG_FILENAME
    catalog = Catalog.load(resolved_catalog)
    wanted_session = slugify(session_filter).casefold() if session_filter else None

    report = PositionsReport()
    for key in sessions_for_season(
        season, round_filter=round_filter, schedule_provider=schedule_provider
    ):
        if wanted_session is not None and slugify(key.session_type).casefold() != wanted_session:
            continue
        catalogued = catalog.has(key)
        target = session_dir(out_dir, key) / f"{POSITIONS_TABLE}.parquet"
        if (not catalogued and not include_uncatalogued) or (target.exists() and not refresh):
            report.skipped.append(key)
            continue
        try:
            table = position_table(loader(key.season, key.round, key.session_type))
        except Exception as exc:  # one bad session must not abort the run
            logger.exception("Failed to load positions for %s", key.catalog_key)
            report.failed.append((key, str(exc)))
            continue
        if table.empty:
            report.failed.append((key, "the archive has no car positions for this session"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(target, engine="pyarrow", index=False, compression="zstd")
        if catalogued:
            catalog.record_table(key, POSITIONS_TABLE, len(table))
            catalog.save(resolved_catalog)
        logger.info("%s: %d position samples", key.catalog_key, len(table))
        report.written.append((key, len(table)))
    return report
