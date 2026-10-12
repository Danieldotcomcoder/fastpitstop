"""Check our championship standings against an independent source.

Compares `GET /standings` (built from our backfill's official results) with
Jolpica, the community-run successor of the Ergast API, after every round
both have: each driver's position, points and wins, and each team's
position and points. Team names differ between the two (F1's "Red Bull
Racing" is Jolpica's "Red Bull"), so teams are compared by place.

    uv run python deploy/check_standings.py --seasons 2025 2026
    uv run python deploy/check_standings.py --api http://127.0.0.1:8000

Exit status 1 on any difference in points or wins, or in the place of a
driver who has points. Drivers on 0 points with no classified finish are
level under the FIA countback and sources order them differently: those
places are printed, not failed. Needs the network; not part of CI.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any

JOLPICA = "https://api.jolpi.ca/ergast/f1"


def _get(url: str) -> Any:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def _jolpica(season: int, round_number: int, kind: str) -> list[dict[str, Any]]:
    # Jolpica's free tier allows a few requests a second.
    time.sleep(0.4)
    body = _get(f"{JOLPICA}/{season}/{round_number}/{kind}standings/")
    lists = body["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0][f"{kind.capitalize()}Standings"] if lists else []


def check_season(api: str, season: int) -> list[str]:
    problems: list[str] = []
    rounds = [r["round"] for r in _get(f"{api}/standings?season={season}")["rounds"]]
    for round_number in rounds:
        ours = _get(f"{api}/standings?season={season}&after_round={round_number}")
        drivers = {d["code"]: (d["position"], d["points"], d["wins"]) for d in ours["drivers"]}
        # Ergast leaves a driver it does not rank without a "position"
        # ("positionText": "-"); compare those on points and wins only.
        official = {
            s["Driver"]["code"]: (
                int(s["position"])
                if "position" in s
                else drivers.get(s["Driver"]["code"], (0,))[0],
                float(s["points"]),
                int(s["wins"]),
            )
            for s in _jolpica(season, round_number, "driver")
        }
        unranked = [
            s["Driver"]["code"]
            for s in _jolpica(season, round_number, "driver")
            if "position" not in s
        ]
        if unranked:
            print(f"{season} round {round_number}: unranked by Jolpica: {unranked}")
        if not official:
            problems.append(f"{season} round {round_number}: no official standings to compare")
            continue
        for code in sorted(set(drivers) | set(official)):
            mine, theirs = drivers.get(code), official.get(code)
            if mine == theirs:
                continue
            if mine and theirs and mine[1:] == theirs[1:] and mine[1] == 0:
                # Same points (none) and wins: only the place differs. The
                # FIA countback leaves drivers with no points and no
                # classified finish level, and sources order them
                # differently, so this is reported, not failed.
                print(
                    f"{season} round {round_number} {code}: tied on 0 points, "
                    f"place {mine[0]} here, {theirs[0]} there"
                )
                continue
            problems.append(f"{season} round {round_number} {code}: ours {mine}, official {theirs}")
        teams = [(t["position"], t["points"]) for t in ours["constructors"]]
        official_teams = [
            (int(s["position"]), float(s["points"]))
            for s in _jolpica(season, round_number, "constructor")
        ]
        if teams != official_teams:
            problems.append(
                f"{season} round {round_number} teams: ours {teams}, official {official_teams}"
            )
        print(f"{season} round {round_number}: {len(drivers)} drivers, {len(teams)} teams checked")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="https://fastpitstop.online/api")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2026])
    args = parser.parse_args()
    problems: list[str] = []
    for season in args.seasons:
        problems += check_season(args.api.rstrip("/"), season)
    for problem in problems:
        print("DIFF", problem)
    print("standings check:", "OK" if not problems else f"{len(problems)} differences")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
