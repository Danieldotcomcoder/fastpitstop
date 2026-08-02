"""Locating and loading one session's historical parquet tables.

Bridges the two `SessionKey`s: the domain `f1_domain.SessionKey` (year,
round, `SessionType` enum) that events carry, and `f1_backfill`'s on-disk
partition layout (`season=/round=/session=<fastf1-slug>`). It also
reconstructs an absolute session-start instant, because FastF1's per-sample
tables (`laps`, `weather`) carry only a session-relative `Time` timedelta —
see `session_start_utc`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from f1_domain import SessionKey, SessionType

# domain SessionType -> the fastf1 session-name slug(s) f1_backfill's writer
# produces (via `slugify`). A tuple because the sprint-quali session was
# renamed across seasons ("Sprint Shootout" in 2023, "Sprint Qualifying"
# from 2024) and both spellings exist on disk.
_SESSION_SLUGS: dict[SessionType, tuple[str, ...]] = {
    SessionType.PRACTICE_1: ("Practice_1",),
    SessionType.PRACTICE_2: ("Practice_2",),
    SessionType.PRACTICE_3: ("Practice_3",),
    SessionType.QUALIFYING: ("Qualifying",),
    SessionType.SPRINT: ("Sprint",),
    SessionType.SPRINT_QUALIFYING: ("Sprint_Qualifying", "Sprint_Shootout"),
    SessionType.RACE: ("Race",),
}

# Every table f1_backfill may write (some are OpenF1-only and absent when
# OpenF1 had no matching session — see DATA_CONTRACTS.md).
_TABLES = (
    "laps",
    "stints",
    "results",
    "weather",
    "race_control",
    "team_radio",
    "intervals",
    "car_telemetry",
)


@dataclass(frozen=True, slots=True)
class SessionTables:
    """The loaded (present) tables for one session, plus the reconstructed
    absolute session start. Missing tables are simply absent from `tables`."""

    session_key: SessionKey
    directory: Path
    tables: dict[str, pd.DataFrame]
    session_start_utc: datetime | None

    def get(self, name: str) -> pd.DataFrame | None:
        return self.tables.get(name)


def session_directory(historical_root: Path, key: SessionKey) -> Path:
    """The partition directory for `key`, trying each candidate fastf1 slug.
    Returns the first that exists; if none do, returns the primary-slug path
    (so the caller gets a clear "not found" against a sensible path)."""
    slugs = _SESSION_SLUGS.get(key.session_type, ())
    base = historical_root / f"season={key.year}" / f"round={key.round:02d}"
    for slug in slugs:
        candidate = base / f"session={slug}"
        if candidate.is_dir():
            return candidate
    primary = slugs[0] if slugs else key.session_type.value
    return base / f"session={primary}"


def load_session_tables(historical_root: Path, key: SessionKey) -> SessionTables:
    """Load every present parquet table for `key` and reconstruct the
    session start. Raises `FileNotFoundError` if the session dir is absent."""
    directory = session_directory(historical_root, key)
    if not directory.is_dir():
        raise FileNotFoundError(f"no historical session at {directory}")

    tables: dict[str, pd.DataFrame] = {}
    for name in _TABLES:
        path = directory / f"{name}.parquet"
        if path.is_file():
            tables[name] = pd.read_parquet(path)

    return SessionTables(
        session_key=key,
        directory=directory,
        tables=tables,
        session_start_utc=_session_start(tables),
    )


def _session_start(tables: dict[str, pd.DataFrame]) -> datetime | None:
    """Reconstruct the absolute session start.

    FastF1 laps carry both an absolute `LapStartDate` and the session-
    relative `LapStartTime`; their difference is the session's t0, and it is
    what lets the `Time`-only tables (weather, and laps' own completion
    `Time`) be placed on an absolute clock. Falls back to the earliest
    absolute OpenF1 `date` if laps are unavailable.
    """
    laps = tables.get("laps")
    if laps is not None and {"LapStartDate", "LapStartTime"}.issubset(laps.columns):
        start = laps["LapStartDate"] - laps["LapStartTime"]
        start = start.dropna()
        if not start.empty:
            return _to_utc(start.median())

    for name in ("race_control", "team_radio", "intervals"):
        frame = tables.get(name)
        if frame is not None and "date" in frame.columns and not frame["date"].empty:
            parsed = pd.to_datetime(frame["date"], utc=True, errors="coerce").dropna()
            if not parsed.empty:
                return _to_utc(parsed.min())
    return None


def _to_utc(value: object) -> datetime:
    """Coerce a pandas Timestamp (tz-naive assumed UTC, tz-aware converted)
    into a tz-aware python `datetime`."""
    ts = pd.Timestamp(value)  # type: ignore[arg-type]
    ts = ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)
    return ts.to_pydatetime()
