"""Championship standings (ROADMAP M2): drivers and constructors after a
round the viewer chooses. The page asks for standings "after the last race
you watched"; nothing past that round is counted or sent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from f1_api.config import Settings, get_settings
from f1_api.routes.schedule import ScheduleCacheDep
from f1_api.schedule import ScheduledEvent, ScheduleUnavailableError
from f1_api.schemas import (
    ConstructorStandingOut,
    DriverStandingOut,
    RemainingPointsOut,
    StandingsOut,
    StandingsRoundOut,
)
from f1_api.standings import (
    compute_standings,
    race_max_points,
    season_rounds,
    sprint_max_points,
)
from f1_domain import SessionType
from f1_kernel import HistoricalSource

router = APIRouter(tags=["standings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _calendar(cache: ScheduleCacheDep, season: int) -> list[ScheduledEvent]:
    try:
        return cache.events(season)
    except ScheduleUnavailableError:
        return []


def _race_start(event: ScheduledEvent | None) -> datetime | None:
    if event is None:
        return None
    return next((s.start_utc for s in event.sessions if s.session_type is SessionType.RACE), None)


def _remaining(
    events: list[ScheduledEvent], season: int, after_round: int
) -> RemainingPointsOut | None:
    if not events:
        return None
    ahead = [e for e in events if e.round > after_round]
    sprints = sum(1 for e in ahead if "sprint" in e.event_format)
    race_points = race_max_points(season)
    sprint_points = sprint_max_points(season)
    return RemainingPointsOut(
        races=len(ahead),
        sprints=sprints,
        race_points=race_points,
        sprint_points=sprint_points,
        max_points=len(ahead) * race_points + sprints * sprint_points,
    )


@router.get("/standings", response_model=StandingsOut)
def get_standings(
    settings: SettingsDep,
    cache: ScheduleCacheDep,
    season: Annotated[int | None, Query(ge=2018, le=2100)] = None,
    after_round: Annotated[int | None, Query(ge=0, le=40)] = None,
) -> StandingsOut:
    """Drivers' and constructors' standings for `season` (default: this
    year) counting rounds up to `after_round` (default: every round with
    results). Built from the backfilled results, so a round counts once
    its Grand Prix has been archived."""
    year = season if season is not None else datetime.now(UTC).year
    # The backfill's own catalog, not the merged session list: a race the
    # store serves (ingested live) is still backfilled, and its official
    # results are here.
    races = sorted(
        {
            (ref.session_key.round, ref.event_name)
            for ref in HistoricalSource(settings.historical_root).sessions()
            if ref.session_key.year == year and ref.session_key.session_type is SessionType.RACE
        }
    )
    rounds = season_rounds(settings.historical_root, year, races)
    standings = compute_standings(rounds, after_round)
    calendar = _calendar(cache, year)
    by_round = {event.round: event for event in calendar}
    return StandingsOut(
        season=year,
        after_round=standings.after_round,
        rounds=[
            StandingsRoundOut(
                round=r.round,
                event_name=r.event_name,
                sprint=r.sprint is not None,
                race_start_utc=_race_start(by_round.get(r.round)),
            )
            for r in rounds
        ],
        drivers=[
            DriverStandingOut(
                position=i,
                code=d.code,
                name=d.name,
                team=d.team,
                team_color=d.team_color,
                points=d.points,
                wins=d.wins,
                by_round=d.by_round,
            )
            for i, d in enumerate(standings.drivers, start=1)
        ],
        constructors=[
            ConstructorStandingOut(
                position=i,
                team=t.name,
                team_color=t.team_color,
                points=t.points,
                wins=t.wins,
                by_round=t.by_round,
            )
            for i, t in enumerate(standings.constructors, start=1)
        ],
        remaining=_remaining(calendar, year, standings.after_round),
    )
