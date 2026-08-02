"""Enumerates `TeamRadioClip` events for a season, sourced from
`f1_backfill`'s own catalog (`manifest.json`) plus
`f1_historical_adapter.events_from_parquet` -- the one existing seam from
parquet to `DomainEvent`s, reused rather than a second radio-only parquet
reader. Team radio is a small channel per session, so this loads each
session's full event stream and filters to `TeamRadioClip`, matching
`events_from_parquet`'s own "one code path" precedent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from f1_backfill.catalog import CATALOG_FILENAME, Catalog
from f1_domain import SessionKey, SessionType, TeamRadioClip
from f1_historical_adapter import events_from_parquet

logger = logging.getLogger(__name__)

# The catalog carries FastF1's own spelling ("Practice 1", "Sprint
# Shootout"); normalize it onto the domain enum. A small local copy rather
# than importing f1_kernel's own `session_type_from_label` (f1_kernel is an
# analytics-kernel dependency this acquisition-only package has no other
# reason to carry) -- f1_pipeline keeps an independent copy for the same
# reason.
_SESSION_TYPE_ALIASES: dict[str, SessionType] = {
    "practice1": SessionType.PRACTICE_1,
    "practice2": SessionType.PRACTICE_2,
    "practice3": SessionType.PRACTICE_3,
    "qualifying": SessionType.QUALIFYING,
    "sprint": SessionType.SPRINT,
    "sprintqualifying": SessionType.SPRINT_QUALIFYING,
    "sprintshootout": SessionType.SPRINT_QUALIFYING,
    "race": SessionType.RACE,
}


def _session_type_from_label(raw: str) -> SessionType:
    normalized = raw.strip().casefold().replace(" ", "").replace("_", "").replace("-", "")
    return _SESSION_TYPE_ALIASES.get(normalized, SessionType.UNKNOWN)


def season_session_keys(
    historical_root: Path, season: int, *, round_filter: int | None = None
) -> list[SessionKey]:
    """Every session key present in `f1_backfill`'s catalog for `season`
    (optionally restricted to one `round_filter`), sorted. Returns an empty
    list if the catalog is absent -- a machine with no backfill yet is a
    normal state, not an error.
    """
    catalog_path = Path(historical_root) / CATALOG_FILENAME
    if not catalog_path.is_file():
        return []
    catalog = Catalog.load(catalog_path)

    keys: list[SessionKey] = []
    for entry in catalog.sessions.values():
        if entry.season != season:
            continue
        if round_filter is not None and entry.round != round_filter:
            continue
        keys.append(
            SessionKey(
                year=entry.season,
                round=entry.round,
                session_type=_session_type_from_label(entry.session_type),
            )
        )
    return sorted(keys, key=lambda k: (k.year, k.round, k.session_type.value))


def season_team_radio_events(
    historical_root: Path, season: int, *, round_filter: int | None = None
) -> Iterator[TeamRadioClip]:
    """Every `TeamRadioClip` event for `season` (optionally one round),
    across every backfilled session, in catalog order. A session listed in
    the catalog whose parquet directory has since gone missing is logged and
    skipped rather than aborting the whole season -- the same per-unit
    isolation `f1_backfill.pipeline.run_backfill` gives each session.
    """
    for key in season_session_keys(historical_root, season, round_filter=round_filter):
        try:
            events = events_from_parquet(historical_root, key)
        except FileNotFoundError:
            logger.warning("Catalog lists %s but its historical data is missing; skipping", key)
            continue
        for event in events:
            if isinstance(event, TeamRadioClip):
                yield event
