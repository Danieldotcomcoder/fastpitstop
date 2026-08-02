"""Enumerates which (season, round, session type) tuples to back-fill.

Wraps `fastf1.get_event_schedule`, which returns one row per round with up
to five session columns (`Session1`..`Session5` — practice/qualifying/race,
or the sprint-weekend variants). `schedule_provider` is injectable so tests
never hit the network.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import fastf1
import pandas as pd

ScheduleProvider = Callable[[int], pd.DataFrame]

_MAX_SESSIONS_PER_ROUND = 5


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Identifies one session to back-fill. `location` is carried along
    purely to help map onto OpenF1's `session_key` later (see
    `f1_backfill.openf1.OpenF1Client.find_session_key`) — it is not part of
    the catalog/partition identity, which is `(season, round, session_type)`.
    """

    season: int
    round: int
    event_name: str
    session_type: str
    location: str

    @property
    def catalog_key(self) -> str:
        return f"{self.season}_{self.round:02d}_{slugify(self.session_type)}"


def default_schedule_provider(season: int) -> pd.DataFrame:
    # fastf1's EventSchedule is a DataFrame subclass; the plain constructor
    # normalizes it to a base DataFrame so nothing downstream depends on
    # fastf1-specific accessor behavior.
    return pd.DataFrame(fastf1.get_event_schedule(season, include_testing=False))


def sessions_for_season(
    season: int,
    *,
    round_filter: int | None = None,
    schedule_provider: ScheduleProvider = default_schedule_provider,
) -> list[SessionKey]:
    """Return every `SessionKey` for `season`, optionally restricted to one
    `round_filter`. Skips round 0 (pre-season testing; already excluded by
    `include_testing=False` in the default provider, but a custom
    `schedule_provider` might not filter it) and any session column that is
    blank for a given round (not every round runs 5 sessions).
    """
    schedule = schedule_provider(season)
    keys: list[SessionKey] = []
    for _, row in schedule.iterrows():
        round_no = int(cast(int, row["RoundNumber"]))
        if round_no <= 0:
            continue
        if round_filter is not None and round_no != round_filter:
            continue
        event_name = str(row["EventName"])
        location = str(row.get("Location", ""))
        for i in range(1, _MAX_SESSIONS_PER_ROUND + 1):
            session_name = row.get(f"Session{i}")
            if not _has_value(session_name):
                continue
            keys.append(
                SessionKey(
                    season=season,
                    round=round_no,
                    event_name=event_name,
                    session_type=str(session_name),
                    location=location,
                )
            )
    return keys


def _has_value(value: Any) -> bool:
    # Blank session columns come back as `None` or NaN (a bare float), not
    # an empty string - `pandas.isna` also matches but its stub overloads
    # don't resolve cleanly against an `Any` input, so this stays explicit.
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return bool(str(value).strip())


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_")
    return slug or "session"
