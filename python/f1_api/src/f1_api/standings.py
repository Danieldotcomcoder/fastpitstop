"""Championship standings (ROADMAP M2), from the backfilled race and sprint
results: each driver's and constructor's points after every round, up to a
round the viewer chooses, so the page never shows a race they haven't
watched (the cutoff is the viewer's, the API only applies it).

Points are the official ones the results carry (`Points`), never
recomputed from positions: penalties and disqualifications are already in
them. Ties are split the FIA way, by countback of Grand Prix finishing
positions (sprints don't count)."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from f1_domain import SessionKey, SessionType
from f1_historical_adapter.reader import session_directory


@dataclass(frozen=True, slots=True)
class RoundResults:
    """One round's classified results: the Grand Prix, and its sprint when
    the weekend had one."""

    round: int
    event_name: str | None
    race: pd.DataFrame
    sprint: pd.DataFrame | None = None


@dataclass(slots=True)
class Standing:
    key: str
    name: str
    team: str | None
    team_color: str | None
    points: float = 0.0
    wins: int = 0
    by_round: list[float] = field(default_factory=lambda: list[float]())
    finishes: defaultdict[int, int] = field(default_factory=lambda: defaultdict[int, int](int))
    """Grand Prix finishing position -> how many times, for countback."""
    code: str = ""


@dataclass(frozen=True, slots=True)
class Standings:
    drivers: list[Standing]
    constructors: list[Standing]
    after_round: int
    """The last round counted (0 when none is)."""


def _points(frame: pd.DataFrame | None) -> dict[str, float]:
    if frame is None or frame.empty or "Points" not in frame:
        return {}
    points = pd.to_numeric(frame["Points"], errors="coerce").fillna(0.0)
    return {str(code): float(p) for code, p in zip(frame["Abbreviation"], points, strict=True)}


def _finish_positions(frame: pd.DataFrame) -> dict[str, int]:
    """Each classified driver's Grand Prix finishing position. An
    unclassified car ("R", "D", ...) has none, and so no countback credit."""
    column = "ClassifiedPosition" if "ClassifiedPosition" in frame else "Position"
    positions = pd.to_numeric(frame[column], errors="coerce")
    return {
        str(code): int(p)
        for code, p in zip(frame["Abbreviation"], positions, strict=True)
        if not math.isnan(p)
    }


def _text(row: pd.Series, column: str) -> str | None:  # type: ignore[type-arg]
    value = row.get(column)
    return str(value) if isinstance(value, str) and value else None


def _color(row: pd.Series) -> str | None:  # type: ignore[type-arg]
    value = _text(row, "TeamColor")
    if value is None or value.lower() == "nan":
        return None
    return value if value.startswith("#") else f"#{value}"


def _ranked(table: dict[str, Standing]) -> list[Standing]:
    """Points, then the FIA countback: most wins, then most seconds, ..."""
    deepest = max((p for s in table.values() for p in s.finishes), default=0)

    def order(s: Standing) -> tuple[float, ...]:
        return (-s.points, *(-float(s.finishes.get(p, 0)) for p in range(1, deepest + 1)))

    return sorted(table.values(), key=lambda s: (order(s), s.name))


def compute_standings(rounds: list[RoundResults], after_round: int | None = None) -> Standings:
    """Standings after `after_round` (every round when None): points summed
    over each counted round's Grand Prix and sprint, `by_round` the running
    total after each counted round. A driver or team first scoring later
    in the season carries zeros for the rounds before."""
    counted = sorted(
        (r for r in rounds if after_round is None or r.round <= after_round),
        key=lambda r: r.round,
    )
    drivers: dict[str, Standing] = {}
    teams: dict[str, Standing] = {}
    for index, result in enumerate(counted):
        race_points = _points(result.race)
        sprint_points = _points(result.sprint)
        finishes = _finish_positions(result.race)
        round_team: dict[str, str] = {}
        for frame in (result.race, result.sprint):
            if frame is None:
                continue
            for _, row in frame.iterrows():
                code = str(row["Abbreviation"])
                team_key = _text(row, "TeamId") or _text(row, "TeamName") or "unknown"
                round_team.setdefault(code, team_key)
                team_name = _text(row, "TeamName") or team_key
                driver = drivers.get(code)
                if driver is None:
                    name = _text(row, "LastName") or _text(row, "BroadcastName") or code
                    driver = drivers[code] = Standing(
                        key=code, code=code, name=name, team=None, team_color=None
                    )
                    driver.by_round = [0.0] * index
                if frame is result.race:
                    # The team and colour of the latest Grand Prix: a driver
                    # who moved mid-season is shown with the team they race
                    # for now.
                    driver.team = team_name
                    driver.team_color = _color(row) or driver.team_color
                team = teams.get(team_key)
                if team is None:
                    team = teams[team_key] = Standing(
                        key=team_key, name=team_name, team=team_name, team_color=None
                    )
                    team.by_round = [0.0] * index
                team.name = team_name
                team.team = team_name
                team.team_color = _color(row) or team.team_color
        for code, team_key in round_team.items():
            gained = race_points.get(code, 0.0) + sprint_points.get(code, 0.0)
            drivers[code].points += gained
            teams[team_key].points += gained
            if code in finishes:
                position = finishes[code]
                drivers[code].finishes[position] += 1
                teams[team_key].finishes[position] += 1
                if position == 1:
                    drivers[code].wins += 1
                    teams[team_key].wins += 1
        for standing in (*drivers.values(), *teams.values()):
            standing.by_round.append(standing.points)
    return Standings(
        drivers=_ranked(drivers),
        constructors=_ranked(teams),
        after_round=counted[-1].round if counted else 0,
    )


def race_max_points(season: int) -> int:
    """The most one driver can score in a Grand Prix: 25 for the win, plus
    the fastest-lap point the regulations awarded from 2019 to 2024."""
    return 26 if 2019 <= season <= 2024 else 25


def sprint_max_points(season: int) -> int:
    """The most one driver can score in a sprint: 8 since 2022, 3 in 2021."""
    return 8 if season >= 2022 else 3


_results_cache: dict[tuple[str, SessionKey], pd.DataFrame | None] = {}
_results_lock = threading.Lock()


def session_results(historical_root: Path, key: SessionKey) -> pd.DataFrame | None:
    """A backfilled session's results table, or None when it isn't there.
    Kept once read: a backfilled session's results never change."""
    cache_key = (str(historical_root), key)
    with _results_lock:
        if cache_key in _results_cache:
            return _results_cache[cache_key]
    # Read the one table directly: the reader's `load_session_tables` also
    # loads laps and race control to rebuild the session clock, which a
    # season's standings would pay for some thirty times over.
    path = session_directory(historical_root, key) / "results.parquet"
    frame = pd.read_parquet(path) if path.is_file() else None
    if frame is not None and (frame.empty or "Abbreviation" not in frame):
        frame = None
    if frame is not None:
        with _results_lock:
            _results_cache[cache_key] = frame
    return frame


def season_rounds(
    historical_root: Path, season: int, races: list[tuple[int, str | None]]
) -> list[RoundResults]:
    """Every round of `season` with Grand Prix results, given the rounds the
    backfill lists as `(round, event_name)`."""
    rounds: list[RoundResults] = []
    for round_number, event_name in races:
        race = session_results(
            historical_root,
            SessionKey(year=season, round=round_number, session_type=SessionType.RACE),
        )
        if race is None:
            continue
        sprint = session_results(
            historical_root,
            SessionKey(year=season, round=round_number, session_type=SessionType.SPRINT),
        )
        rounds.append(RoundResults(round_number, event_name, race, sprint))
    return rounds


__all__ = [
    "RoundResults",
    "Standing",
    "Standings",
    "compute_standings",
    "race_max_points",
    "season_rounds",
    "session_results",
    "sprint_max_points",
]
