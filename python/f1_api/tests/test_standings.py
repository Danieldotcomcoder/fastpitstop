"""Championship standings: points from the official results, sprints
included, ties split by countback, and nothing past the round the viewer
chose. Synthetic tables for the rules, the checked-in kernel fixture for
the route, and an opt-in check against the real 2024 championship."""

from __future__ import annotations

import json
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import f1_api.sessions as sessions_module
from f1_api.config import Settings
from f1_api.main import create_app
from f1_api.standings import RoundResults, compute_standings, season_rounds
from f1_domain import SessionKey, SessionType
from f1_kernel import MemorySource, SessionRef

FIXTURE = Path(__file__).resolve().parents[2] / "f1_kernel/tests/fixtures/historical"
REAL_ROOT = Path(__file__).resolve().parents[3] / "data" / "historical"


def _results(rows: list[tuple[str, str, str | int, float]]) -> pd.DataFrame:
    """(code, team, classified position, points) per car."""
    return pd.DataFrame(
        {
            "Abbreviation": [r[0] for r in rows],
            "LastName": [r[0].title() for r in rows],
            "TeamName": [r[1] for r in rows],
            "TeamColor": ["3671C6" if r[1] == "Red Bull" else "FF8000" for r in rows],
            "ClassifiedPosition": [str(r[2]) for r in rows],
            "Points": [r[3] for r in rows],
        }
    )


ROUNDS = [
    RoundResults(
        1,
        "Bahrain Grand Prix",
        _results(
            [("VER", "Red Bull", 1, 25), ("NOR", "McLaren", 2, 18), ("PER", "Red Bull", "R", 0)]
        ),
    ),
    RoundResults(
        2,
        "Chinese Grand Prix",
        _results(
            [("NOR", "McLaren", 1, 25), ("VER", "Red Bull", 2, 18), ("PER", "Red Bull", 3, 15)]
        ),
        sprint=_results([("NOR", "McLaren", 1, 8), ("VER", "Red Bull", 2, 7)]),
    ),
    RoundResults(
        3,
        "Japanese Grand Prix",
        # LAW replaces PER at Red Bull from round 3.
        _results(
            [("VER", "Red Bull", 1, 25), ("NOR", "McLaren", 2, 18), ("LAW", "Red Bull", 3, 15)]
        ),
    ),
]


def test_points_count_every_grand_prix_and_sprint() -> None:
    standings = compute_standings(ROUNDS)
    drivers = {d.code: d for d in standings.drivers}
    assert standings.after_round == 3
    assert drivers["NOR"].points == 18 + 25 + 8 + 18
    assert drivers["VER"].by_round == [25, 25 + 18 + 7, 25 + 18 + 7 + 25]
    assert (drivers["VER"].wins, drivers["NOR"].wins) == (2, 1)
    # A sprint win is not a win.
    assert drivers["NOR"].wins == 1


def test_a_driver_arriving_mid_season_starts_from_zero() -> None:
    law = next(d for d in compute_standings(ROUNDS).drivers if d.code == "LAW")
    assert law.by_round == [0.0, 0.0, 15.0]
    assert law.team == "Red Bull"


def test_constructors_take_each_cars_points_for_the_team_it_raced() -> None:
    teams = {t.name: t for t in compute_standings(ROUNDS).constructors}
    assert teams["Red Bull"].points == 25 + 0 + 18 + 15 + 7 + 25 + 15
    assert teams["McLaren"].points == 18 + 25 + 8 + 18


def test_nothing_past_the_chosen_round_is_counted() -> None:
    standings = compute_standings(ROUNDS, after_round=1)
    assert standings.after_round == 1
    assert [d.code for d in standings.drivers] == ["VER", "NOR", "PER"]
    assert all(len(d.by_round) == 1 for d in standings.drivers)
    assert all(d.code != "LAW" for d in standings.drivers)


def test_a_tie_on_points_goes_to_the_better_finishes() -> None:
    tie = [
        RoundResults(1, None, _results([("AAA", "McLaren", 2, 18), ("BBB", "Red Bull", 3, 15)])),
        RoundResults(2, None, _results([("BBB", "Red Bull", 2, 18), ("AAA", "McLaren", 3, 15)])),
        RoundResults(3, None, _results([("BBB", "Red Bull", 4, 12), ("AAA", "McLaren", 5, 10)])),
        RoundResults(4, None, _results([("AAA", "McLaren", 4, 12), ("BBB", "Red Bull", 6, 10)])),
    ]
    a, b = compute_standings(tie).drivers
    assert a.points == b.points == 55
    # Equal on seconds, thirds and fourths; AAA also has a fifth.
    assert (a.code, b.code) == ("AAA", "BBB")


def _client(tmp_path: Path, schedule: Mapping[str, object] | None = None) -> TestClient:
    path = None
    if schedule is not None:
        path = tmp_path / "schedule.json"
        path.write_text(json.dumps(schedule), encoding="utf-8")
    return TestClient(create_app(Settings(historical_root=FIXTURE, schedule_fixture_path=path)))


def _event(round_number: int, event_format: str = "conventional") -> dict[str, object]:
    return {
        "round": round_number,
        "event_name": f"Round {round_number}",
        "event_format": event_format,
        "sessions": [
            {"name": "Race", "start_utc": f"2024-03-{round_number + 1:02d}T15:00:00+00:00"}
        ],
    }


def test_the_route_counts_up_to_the_chosen_round(tmp_path: Path) -> None:
    schedule = {"2024": [_event(1), _event(2), _event(3, "sprint_qualifying"), _event(4)]}
    client = _client(tmp_path, schedule)
    full = client.get("/standings", params={"season": 2024}).json()
    assert [r["round"] for r in full["rounds"]] == [1, 2]
    assert full["rounds"][1]["race_start_utc"] == "2024-03-03T15:00:00Z"
    assert full["after_round"] == 2
    assert full["drivers"][0]["code"] == "VER"
    assert len(full["drivers"][0]["by_round"]) == 2
    # Two rounds left after round 2, one of them a sprint weekend; 2024
    # still had the fastest-lap point.
    assert full["remaining"] == {
        "races": 2,
        "sprints": 1,
        "race_points": 26,
        "sprint_points": 8,
        "max_points": 60,
    }

    first = client.get("/standings", params={"season": 2024, "after_round": 1}).json()
    assert first["after_round"] == 1
    # Every round with results is still offered to choose from.
    assert [r["round"] for r in first["rounds"]] == [1, 2]
    assert all(len(d["by_round"]) == 1 for d in first["drivers"])
    assert first["remaining"]["races"] == 3


def test_without_a_calendar_there_is_no_points_still_available(tmp_path: Path) -> None:
    body = _client(tmp_path, {}).get("/standings", params={"season": 2024}).json()
    assert body["remaining"] is None


@pytest.mark.skipif(
    not (REAL_ROOT / "season=2024" / "round=24").is_dir(), reason="2024 backfill not present"
)
def test_matches_the_official_2024_championship() -> None:
    rounds = season_rounds(REAL_ROOT, 2024, [(r, None) for r in range(1, 25)])
    standings = compute_standings(rounds)
    drivers = {d.code: d.points for d in standings.drivers}
    assert [d.code for d in standings.drivers[:5]] == ["VER", "NOR", "LEC", "PIA", "SAI"]
    assert (drivers["VER"], drivers["NOR"], drivers["LEC"]) == (437, 374, 356)
    teams = [(t.name, t.points) for t in standings.constructors[:3]]
    assert teams == [("McLaren", 666), ("Ferrari", 652), ("Red Bull Racing", 589)]


def test_a_race_the_store_serves_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026 rounds 14 to 17 were ingested live, so the session list names the
    store for them; their official results are in the backfill all the same."""
    live_race = SessionKey(year=2024, round=2, session_type=SessionType.RACE)

    class _Store(MemorySource):
        def sessions(self) -> list[SessionRef]:
            return [SessionRef(session_key=live_race, event_name="Saudi Arabian Grand Prix")]

    @contextmanager
    def fake_store(_: Settings) -> Generator[sessions_module._Store | None, None, None]:  # pyright: ignore[reportPrivateUsage]
        yield sessions_module._Store(source=_Store([]), live_keys=frozenset())  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(sessions_module, "_store_source", fake_store)
    body = _client(tmp_path).get("/standings", params={"season": 2024}).json()
    assert [r["round"] for r in body["rounds"]] == [1, 2]


def test_a_tie_on_no_points_goes_by_the_full_finishing_order() -> None:
    """After 2026 round 1 five drivers had 0 points and no classified
    finish: level under the FIA countback. They are shown in the order they
    dropped out, not alphabetically."""
    race = pd.DataFrame(
        {
            "Abbreviation": ["AAA", "ZZZ", "MMM"],
            "LastName": ["Aaa", "Zzz", "Mmm"],
            "TeamName": ["A", "Z", "M"],
            "Position": [19.0, 17.0, 18.0],
            "ClassifiedPosition": ["R", "R", "R"],
            "Points": [0.0, 0.0, 0.0],
        }
    )
    standings = compute_standings([RoundResults(1, None, race)])
    assert [d.code for d in standings.drivers] == ["ZZZ", "MMM", "AAA"]
