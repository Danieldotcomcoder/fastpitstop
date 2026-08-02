"""Resolving a domain `SessionKey` for a run.

The `SessionKey` (year, round, `SessionType`) is what every event and every
DB row is keyed by, so each entry point has to pin it down:

- replay: the recording `Manifest` gives year and a session-type STRING
  (e.g. "Race", "Practice 1"), but no round — the operator supplies that.
- historical: `f1_backfill`'s partition dirs give season / round / a fastf1
  session SLUG (e.g. "Practice_1"), which maps back to a `SessionType`.
"""

from __future__ import annotations

import re
from pathlib import Path

from f1_domain import SessionKey, SessionType

# Normalized fastf1 session name / slug -> domain SessionType. Keys are
# lowercased with spaces and underscores unified, so "Practice 1",
# "practice_1" and "Practice_1" all resolve.
_SESSION_TYPES: dict[str, SessionType] = {
    "practice 1": SessionType.PRACTICE_1,
    "practice 2": SessionType.PRACTICE_2,
    "practice 3": SessionType.PRACTICE_3,
    "qualifying": SessionType.QUALIFYING,
    "sprint": SessionType.SPRINT,
    "sprint qualifying": SessionType.SPRINT_QUALIFYING,
    "sprint shootout": SessionType.SPRINT_QUALIFYING,
    "race": SessionType.RACE,
}


def session_type_from_name(name: str) -> SessionType:
    """Map a fastf1 session name or partition slug onto a `SessionType`.
    Unrecognized names resolve to `SessionType.UNKNOWN` (never guess)."""
    normalized = name.strip().replace("_", " ").lower()
    return _SESSION_TYPES.get(normalized, SessionType.UNKNOWN)


def session_key(
    *,
    year: int,
    round: int,
    session_type: SessionType | str,
) -> SessionKey:
    """Build a `SessionKey`, accepting either a `SessionType` or a raw
    session-name string for `session_type`."""
    resolved = (
        session_type
        if isinstance(session_type, SessionType)
        else session_type_from_name(session_type)
    )
    return SessionKey(year=year, round=round, session_type=resolved)


_ROUND_DIR = re.compile(r"^round=(\d+)$")
_SESSION_DIR = re.compile(r"^session=(.+)$")


def discover_historical_sessions(
    historical_root: Path, season: int, *, round_filter: int | None = None
) -> list[SessionKey]:
    """Enumerate the `SessionKey`s present under `historical_root` for one
    season by scanning `f1_backfill`'s partition layout
    (`season=/round=/session=<slug>`). Sessions whose slug doesn't map to a
    known `SessionType` are skipped. Sorted by (round, session_type)."""
    season_dir = historical_root / f"season={season}"
    if not season_dir.is_dir():
        return []

    keys: list[SessionKey] = []
    for round_dir in sorted(season_dir.iterdir()):
        match = _ROUND_DIR.match(round_dir.name)
        if not match or not round_dir.is_dir():
            continue
        round_no = int(match.group(1))
        if round_filter is not None and round_no != round_filter:
            continue
        for session_dir in sorted(round_dir.iterdir()):
            session_match = _SESSION_DIR.match(session_dir.name)
            if not session_match or not session_dir.is_dir():
                continue
            session_type = session_type_from_name(session_match.group(1))
            if session_type is SessionType.UNKNOWN:
                continue
            keys.append(SessionKey(year=season, round=round_no, session_type=session_type))
    return sorted(keys, key=lambda k: (k.round, k.session_type.value))
