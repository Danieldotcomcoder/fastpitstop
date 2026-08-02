"""Regenerate the checked-in Phase 2 test fixture.

    uv run python python/f1_kernel/fixtures/generate.py

Writes two synthetic historical sessions in `f1_backfill`'s EXACT on-disk
shape (FastF1/OpenF1 column names, dtypes and null conventions, verified
against a real 2024 backfill) plus `truth.json`, the exact ground truth for
each.

Why synthetic rather than a slice of a real race: `data/` is gitignored on
purpose — this is an unofficial fan project and F1's data does not belong
in the repo. Synthesizing lets the tests assert EXACT outcomes (which laps
ran under the safety car, which laps are dirty air, which lap is the
planted anomaly) instead of plausibility ranges, and it runs in CI on a
machine with no backfill at all. Tests that want the real thing are
opt-in and skip when `data/historical/` is absent — see
`tests/test_real_historical.py`.

The two sessions are deliberately different:

- **round 1** — a safety car (laps 18-20), one stop each, a driver stuck in
  another's wake for six laps, one planted anomalous lap, and one in-lap
  with no lap time (the feed drops it; real backfills contain these).
- **round 2** — no safety car, a sector yellow instead, different pit laps.
  Two sessions is the minimum for a meaningful backtest-harness test.

Everything is deterministic: fixed timings, a seeded jitter sequence, and
no wall-clock reads. Regenerating produces identical files.
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

OUTPUT_ROOT = Path(__file__).parents[1] / "tests" / "fixtures" / "historical"
TRUTH_PATH = Path(__file__).parents[1] / "tests" / "fixtures" / "truth.json"

SESSION_START = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)

DRIVERS: tuple[tuple[str, str, str], ...] = (
    ("1", "VER", "Red Bull Racing"),
    ("11", "PER", "Red Bull Racing"),
    ("16", "LEC", "Ferrari"),
    ("55", "SAI", "Ferrari"),
    ("44", "HAM", "Mercedes"),
    ("63", "RUS", "Mercedes"),
    ("4", "NOR", "McLaren"),
    ("81", "PIA", "McLaren"),
)

BASE_LAP_S = 90.0
PACE_STEP_S = 0.35
"""Each grid slot is this much slower, so the field spreads out."""
DEG_PER_TYRE_LAP_S = 0.05
LAP1_PENALTY_S = 8.0
PIT_IN_PENALTY_S = 2.5
PIT_OUT_PENALTY_S = 20.0
SAFETY_CAR_PENALTY_S = 25.0
YELLOW_PENALTY_S = 3.0

CLEAR_AIR_GAP_S = 2.6
DIRTY_AIR_GAP_S = 0.75
GAP_SAMPLE_INTERVAL_S = 5.0

TELEMETRY_HZ = 5.0
TELEMETRY_LAPS = 8
"""Telemetry is generated for one driver over the first few laps only —
enough to exercise lap tagging and distance integration without checking a
race's worth of samples into git."""


@dataclass(frozen=True)
class Plan:
    """Everything that makes one session different from another."""

    season: int
    round_no: int
    event_name: str
    total_laps: int
    pit_laps: dict[str, int]
    safety_car: tuple[int, int] | None = None
    """(first lap under SC, last lap under SC), by lap number."""
    yellow: tuple[int, int] | None = None
    """(first lap, last lap) of a sector yellow."""
    dirty_air: tuple[str, int, int] | None = None
    """(driver, first lap, last lap) spent within DIRTY_AIR_GAP_S of the car ahead."""
    anomaly: tuple[str, int, float] | None = None
    """(driver, lap, seconds added) — a planted lock-up/spin."""
    missing_lap_time: tuple[str, int] | None = None
    """(driver, lap) whose LapTime the feed dropped."""
    telemetry_driver: str | None = None


PLANS: tuple[Plan, ...] = (
    Plan(
        season=2024,
        round_no=1,
        event_name="Bahrain Grand Prix",
        total_laps=30,
        pit_laps={"1": 14, "11": 15, "16": 13, "55": 16, "44": 14, "63": 15, "4": 12, "81": 16},
        safety_car=(18, 20),
        dirty_air=("55", 5, 10),
        anomaly=("11", 24, 6.0),
        missing_lap_time=("63", 15),
        telemetry_driver="1",
    ),
    Plan(
        season=2024,
        round_no=2,
        event_name="Saudi Arabian Grand Prix",
        total_laps=26,
        pit_laps={"1": 12, "11": 13, "16": 11, "55": 14, "44": 12, "63": 13, "4": 10, "81": 14},
        yellow=(7, 8),
        dirty_air=("81", 16, 20),
        anomaly=("4", 22, 5.0),
    ),
)


@dataclass
class LapRecord:
    """One simulated lap, kept in wall-clock terms so ground truth can be
    stated as "which window did this status overlap"."""

    driver: str
    lap_number: int
    start_s: float
    end_s: float
    lap_time_s: float | None
    stint: int
    compound: str
    tyre_life: int
    pit_in: bool
    pit_out: bool
    position: int = 0
    sectors: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class SessionBuild:
    laps: list[LapRecord] = field(default_factory=list)
    race_control: list[dict[str, Any]] = field(default_factory=list)
    intervals: list[dict[str, Any]] = field(default_factory=list)
    telemetry: list[dict[str, Any]] = field(default_factory=list)


def main() -> int:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    catalog: dict[str, Any] = {"format_version": 1, "sessions": {}}
    truth: dict[str, Any] = {}

    for plan in PLANS:
        build = simulate(plan)
        tables = build_tables(plan, build)
        directory = (
            OUTPUT_ROOT / f"season={plan.season}" / f"round={plan.round_no:02d}" / "session=Race"
        )
        directory.mkdir(parents=True, exist_ok=True)
        for name, frame in tables.items():
            frame.to_parquet(directory / f"{name}.parquet", index=False)

        key = f"{plan.season}_{plan.round_no:02d}_Race"
        catalog["sessions"][key] = {
            "season": plan.season,
            "round": plan.round_no,
            "session_type": "Race",
            "event_name": plan.event_name,
            "sources": ["fastf1", "openf1"],
            "tables": {name: len(frame) for name, frame in tables.items()},
            "acquired_at": "2024-03-03T00:00:00Z",
        }
        truth[key] = derive_truth(plan, build)
        print(
            f"  {key}: {len(build.laps)} laps, "
            f"{len(build.intervals)} interval samples, "
            f"{len(build.telemetry)} telemetry samples"
        )

    (OUTPUT_ROOT / "manifest.json").write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    TRUTH_PATH.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_ROOT} and {TRUTH_PATH}")
    return 0


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------


def simulate(plan: Plan) -> SessionBuild:
    build = SessionBuild()
    for index, (number, _code, _team) in enumerate(DRIVERS):
        _simulate_driver(plan, build, index, number)
    _assign_positions(build)
    _emit_race_control(plan, build)
    _emit_intervals(plan, build)
    if plan.telemetry_driver is not None:
        _emit_telemetry(plan, build)
    return build


def _simulate_driver(plan: Plan, build: SessionBuild, index: int, number: str) -> None:
    pace = BASE_LAP_S + PACE_STEP_S * index
    pit_lap = plan.pit_laps[number]
    clock = 0.0
    stint = 1
    tyre_life = 1
    compound = "SOFT"

    for lap_number in range(1, plan.total_laps + 1):
        pit_in = lap_number == pit_lap
        pit_out = lap_number == pit_lap + 1
        if pit_out:
            stint = 2
            tyre_life = 1
            compound = "HARD"

        lap_time = pace + DEG_PER_TYRE_LAP_S * (tyre_life - 1)
        if lap_number == 1:
            lap_time += LAP1_PENALTY_S + index * 0.9  # grid spread
        if pit_in:
            lap_time += PIT_IN_PENALTY_S
        if pit_out:
            lap_time += PIT_OUT_PENALTY_S
        if plan.safety_car is not None and plan.safety_car[0] <= lap_number <= plan.safety_car[1]:
            lap_time += SAFETY_CAR_PENALTY_S
        if plan.yellow is not None and plan.yellow[0] <= lap_number <= plan.yellow[1]:
            lap_time += YELLOW_PENALTY_S
        if plan.anomaly is not None and (number, lap_number) == plan.anomaly[:2]:
            lap_time += plan.anomaly[2]
        # A deterministic sub-tenth wobble, so lap times are not identical
        # and the MAD-based outlier bound has something real to measure.
        lap_time += _jitter(index, lap_number)

        start_s = clock
        end_s = clock + lap_time
        clock = end_s

        reported: float | None = lap_time
        if plan.missing_lap_time is not None and (number, lap_number) == plan.missing_lap_time:
            reported = None

        build.laps.append(
            LapRecord(
                driver=number,
                lap_number=lap_number,
                start_s=start_s,
                end_s=end_s,
                lap_time_s=reported,
                stint=stint,
                compound=compound,
                tyre_life=tyre_life,
                pit_in=pit_in,
                pit_out=pit_out,
                sectors=_split_sectors(lap_time),
            )
        )
        tyre_life += 1


def _jitter(index: int, lap_number: int) -> float:
    """A reproducible pseudo-random wobble in [-0.05, 0.05] seconds. A fixed
    formula rather than `random` so the fixture cannot shift if Python's RNG
    implementation ever changes."""
    return 0.05 * math.sin(index * 2.399963 + lap_number * 1.7320508)


def _split_sectors(lap_time: float) -> tuple[float, float, float]:
    """Three sectors that add up to the lap, rounded to the feed's precision."""
    s1 = round(lap_time * 0.31, 3)
    s2 = round(lap_time * 0.36, 3)
    return (s1, s2, round(lap_time - s1 - s2, 3))


def _assign_positions(build: SessionBuild) -> None:
    """Position per lap = order of crossing the line on that lap."""
    by_lap: dict[int, list[LapRecord]] = {}
    for lap in build.laps:
        by_lap.setdefault(lap.lap_number, []).append(lap)
    for laps in by_lap.values():
        for position, lap in enumerate(sorted(laps, key=lambda item: item.end_s), start=1):
            lap.position = position


def _leader_crossing(build: SessionBuild, lap_number: int) -> float:
    """When the first car completed `lap_number` — race control's clock."""
    times = [lap.end_s for lap in build.laps if lap.lap_number == lap_number]
    return min(times) if times else 0.0


def _emit_race_control(plan: Plan, build: SessionBuild) -> None:
    """Race-control messages in OpenF1's exact vocabulary — the same strings
    `f1_kernel.status` reconstructs a track-status timeline from."""
    build.race_control.append(
        _rc_row(
            0.0,
            category="Flag",
            flag="GREEN",
            scope="Track",
            message="GREEN LIGHT - PIT EXIT OPEN",
            lap=1,
        )
    )

    if plan.safety_car is not None:
        first, last = plan.safety_car
        # Deployed just after the leader starts the first SC lap, so every
        # car's lap window from there on overlaps it.
        deploy_s = _leader_crossing(build, first - 1) + 1.0
        build.race_control.append(
            _rc_row(deploy_s, category="SafetyCar", message="SAFETY CAR DEPLOYED", lap=first)
        )
        # "IN THIS LAP" does NOT end it; the following track-scope clear does.
        build.race_control.append(
            _rc_row(
                _leader_crossing(build, last) - 30.0,
                category="SafetyCar",
                message="SAFETY CAR IN THIS LAP",
                lap=last,
            )
        )
        build.race_control.append(
            _rc_row(
                _last_crossing(build, last) + 1.0,
                category="Flag",
                flag="CLEAR",
                scope="Track",
                message="TRACK CLEAR",
                lap=last,
            )
        )

    if plan.yellow is not None:
        first, last = plan.yellow
        build.race_control.append(
            _rc_row(
                _leader_crossing(build, first - 1) + 1.0,
                category="Flag",
                flag="YELLOW",
                scope="Sector",
                message="YELLOW IN TRACK SECTOR 7",
                lap=first,
            )
        )
        build.race_control.append(
            _rc_row(
                _last_crossing(build, last) + 1.0,
                category="Flag",
                flag="CLEAR",
                scope="Sector",
                message="CLEAR IN TRACK SECTOR 7",
                lap=last,
            )
        )

    # A driver-scoped blue flag: track status must NOT react to it.
    build.race_control.append(
        _rc_row(
            _leader_crossing(build, 3) + 5.0,
            category="Flag",
            flag="BLUE",
            scope="Driver",
            message="WAVED BLUE FLAG FOR CAR 81 (PIA)",
            lap=3,
            driver=81.0,
        )
    )
    build.race_control.sort(key=lambda row: row["date"])


def _last_crossing(build: SessionBuild, lap_number: int) -> float:
    times = [lap.end_s for lap in build.laps if lap.lap_number == lap_number]
    return max(times) if times else 0.0


def _rc_row(
    offset_s: float,
    *,
    category: str,
    message: str,
    lap: int,
    flag: str | None = None,
    scope: str | None = None,
    driver: float | None = None,
) -> dict[str, Any]:
    return {
        "meeting_key": 1229,
        "session_key": 9472,
        "date": _iso(offset_s),
        "driver_number": driver,
        "lap_number": lap,
        "category": category,
        "flag": flag,
        "scope": scope,
        "sector": None,
        "qualifying_phase": None,
        "message": message,
    }


def _emit_intervals(plan: Plan, build: SessionBuild) -> None:
    """The gap channel, sampled through each lap — OpenF1 sends these as
    STRINGS ('0.853'), with <NA> where the gap is unknown."""
    dirty_driver, dirty_first, dirty_last = plan.dirty_air or ("", 0, -1)
    for lap in build.laps:
        index = next(i for i, d in enumerate(DRIVERS) if d[0] == lap.driver)
        in_dirty_air = lap.driver == dirty_driver and dirty_first <= lap.lap_number <= dirty_last
        base_gap = DIRTY_AIR_GAP_S if in_dirty_air else CLEAR_AIR_GAP_S + 0.4 * index
        sample_s = lap.start_s + GAP_SAMPLE_INTERVAL_S
        while sample_s <= lap.end_s:
            wobble = 0.04 * math.sin(sample_s * 0.7 + index)
            gap = max(0.05, base_gap + wobble)
            build.intervals.append(
                {
                    "date": _iso(sample_s),
                    "session_key": 9472,
                    "gap_to_leader": None if index == 0 else f"{gap * index:.3f}",
                    "meeting_key": 1229,
                    "driver_number": int(lap.driver),
                    # The leader has no car ahead: the channel is null, not zero.
                    "interval": None if index == 0 else f"{gap:.3f}",
                }
            )
            sample_s += GAP_SAMPLE_INTERVAL_S
    build.intervals.sort(key=lambda row: (row["date"], row["driver_number"]))


def _emit_telemetry(plan: Plan, build: SessionBuild) -> None:
    """A plausible speed/throttle/brake/gear/DRS trace for one driver.

    Two braking zones per lap, speed shaped so integrating it recovers
    roughly the right lap distance — enough for the distance/lap-tagging
    columns to be tested without pretending to be a real circuit.
    """
    driver = plan.telemetry_driver
    laps = [lap for lap in build.laps if lap.driver == driver and lap.lap_number <= TELEMETRY_LAPS]
    step = 1.0 / TELEMETRY_HZ
    for lap in laps:
        sample_s = lap.start_s
        while sample_s < lap.end_s:
            phase = (sample_s - lap.start_s) / max(lap.end_s - lap.start_s, 1e-6)
            # Two "corners": speed dips hard around 35% and 75% of the lap.
            dip = min(
                1.0,
                abs(math.sin(math.pi * (phase - 0.35) * 3.0)) + 0.25,
            ) * min(1.0, abs(math.sin(math.pi * (phase - 0.75) * 3.0)) + 0.35)
            speed = 90.0 + 220.0 * dip
            braking = speed < 150.0 and phase % 0.4 > 0.3
            build.telemetry.append(
                {
                    "Date": _naive(sample_s),
                    "RPM": int(6000 + 6000 * (speed / 310.0)),
                    "Speed": round(speed, 1),
                    "nGear": max(1, min(8, int(speed / 40) + 1)),
                    "Throttle": 0.0 if braking else round(min(100.0, speed / 2.6), 1),
                    "Brake": bool(braking),
                    "DRS": 12 if (0.05 < phase < 0.2 and speed > 250) else 0,
                    "Source": "car",
                    "Time": pd.Timedelta(seconds=sample_s),
                    "SessionTime": pd.Timedelta(seconds=sample_s),
                    "DriverNumber": driver,
                }
            )
            sample_s += step


def _iso(offset_s: float) -> str:
    return (SESSION_START + timedelta(seconds=offset_s)).isoformat()


def _naive(offset_s: float) -> datetime:
    """FastF1 writes tz-naive UTC timestamps; the adapter assumes UTC."""
    return (SESSION_START + timedelta(seconds=offset_s)).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# parquet tables, in f1_backfill's exact shape
# ---------------------------------------------------------------------------


def build_tables(plan: Plan, build: SessionBuild) -> dict[str, pd.DataFrame]:
    tables = {
        "laps": _laps_table(build),
        "stints": _stints_table(build),
        "results": _results_table(plan, build),
        "weather": _weather_table(plan),
        "race_control": pd.DataFrame(build.race_control),
        "intervals": pd.DataFrame(build.intervals),
    }
    if build.telemetry:
        tables["car_telemetry"] = pd.DataFrame(build.telemetry)
    return tables


def _laps_table(build: SessionBuild) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    codes = {number: code for number, code, _ in DRIVERS}
    teams = {number: team for number, _, team in DRIVERS}
    for lap in sorted(build.laps, key=lambda item: (item.driver, item.lap_number)):
        s1, s2, s3 = lap.sectors
        rows.append(
            {
                "Time": pd.Timedelta(seconds=lap.end_s),
                "Driver": codes[lap.driver],
                "DriverNumber": lap.driver,
                "LapTime": (
                    pd.NaT if lap.lap_time_s is None else pd.Timedelta(seconds=lap.lap_time_s)
                ),
                "LapNumber": float(lap.lap_number),
                "Stint": float(lap.stint),
                "PitOutTime": pd.Timedelta(seconds=lap.start_s + 8.0) if lap.pit_out else pd.NaT,
                "PitInTime": pd.Timedelta(seconds=lap.end_s - 4.0) if lap.pit_in else pd.NaT,
                "Sector1Time": pd.Timedelta(seconds=s1),
                "Sector2Time": pd.Timedelta(seconds=s2),
                "Sector3Time": pd.Timedelta(seconds=s3),
                "Sector1SessionTime": pd.Timedelta(seconds=lap.start_s + s1),
                "Sector2SessionTime": pd.Timedelta(seconds=lap.start_s + s1 + s2),
                "Sector3SessionTime": pd.Timedelta(seconds=lap.end_s),
                "IsPersonalBest": False,
                "Compound": lap.compound,
                "TyreLife": float(lap.tyre_life),
                "FreshTyre": lap.stint == 1,
                "Team": teams[lap.driver],
                "LapStartTime": pd.Timedelta(seconds=lap.start_s),
                "LapStartDate": _naive(lap.start_s),
                "TrackStatus": "4" if lap.lap_time_s and lap.lap_time_s > 110 else "1",
                "Position": float(lap.position),
                "Deleted": None,
                "DeletedReason": None,
                "FastF1Generated": False,
                "IsAccurate": lap.lap_time_s is not None,
            }
        )
    return pd.DataFrame(rows)


def _stints_table(build: SessionBuild) -> pd.DataFrame:
    codes = {number: code for number, code, _ in DRIVERS}
    grouped: dict[tuple[str, int], list[LapRecord]] = {}
    for lap in build.laps:
        grouped.setdefault((lap.driver, lap.stint), []).append(lap)
    rows = [
        {
            "Driver": codes[driver],
            "Stint": float(stint),
            "Compound": laps[0].compound,
            "LapStart": float(min(lap.lap_number for lap in laps)),
            "LapEnd": float(max(lap.lap_number for lap in laps)),
            "LapCount": len(laps),
        }
        for (driver, stint), laps in sorted(grouped.items())
    ]
    return pd.DataFrame(rows)


def _results_table(plan: Plan, build: SessionBuild) -> pd.DataFrame:
    final = {lap.driver: lap.position for lap in build.laps if lap.lap_number == plan.total_laps}
    rows = [
        {
            "DriverNumber": number,
            "BroadcastName": code,
            "Abbreviation": code,
            "DriverId": code.lower(),
            "TeamName": team,
            "Position": float(final.get(number, 0)),
            "ClassifiedPosition": str(final.get(number, 0)),
            "GridPosition": float(index + 1),
            "Status": "Finished",
            "Points": 0.0,
            "Laps": float(plan.total_laps),
        }
        for index, (number, code, team) in enumerate(DRIVERS)
    ]
    return pd.DataFrame(rows).sort_values("Position").reset_index(drop=True)


def _weather_table(plan: Plan) -> pd.DataFrame:
    rows = [
        {
            "Time": pd.Timedelta(seconds=60.0 * minute),
            "AirTemp": 24.0 + 0.1 * minute,
            "Humidity": 40.0,
            "Pressure": 1012.0,
            "Rainfall": False,
            "TrackTemp": 31.0 + 0.2 * minute,
            "WindDirection": 180.0,
            "WindSpeed": 2.0,
        }
        for minute in range(plan.total_laps * 2)
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------


def derive_truth(plan: Plan, build: SessionBuild) -> dict[str, Any]:
    """The exact expected outcomes, stated from the simulation's own
    timeline — never from the kernel's reconstruction of it.

    Safety-car and yellow laps are per driver and derived by OVERLAP of each
    lap's window with the flag window, because that is what "a lap run
    wholly or partly under a non-green status" means. Cars are spread out,
    so the trailing cars' preceding lap overlaps the deployment too — that
    is real, not an artifact.
    """
    truth: dict[str, Any] = {
        "event_name": plan.event_name,
        "total_laps": plan.total_laps,
        "drivers": [number for number, _, _ in DRIVERS],
        "laps_by_driver": {
            number: sum(1 for lap in build.laps if lap.driver == number) for number, _, _ in DRIVERS
        },
        "pit_in_laps": {driver: [lap] for driver, lap in plan.pit_laps.items()},
        "pit_out_laps": {
            driver: [lap + 1] for driver, lap in plan.pit_laps.items() if lap + 1 <= plan.total_laps
        },
        "stint_compounds": {number: ["SOFT", "HARD"] for number, _, _ in DRIVERS},
        "missing_lap_time": list(plan.missing_lap_time) if plan.missing_lap_time else None,
        "anomaly": list(plan.anomaly) if plan.anomaly else None,
        "telemetry_driver": plan.telemetry_driver,
        "telemetry_laps": TELEMETRY_LAPS if plan.telemetry_driver else 0,
    }

    if plan.safety_car is not None:
        window = _flag_window(build, plan.safety_car)
        truth["safety_car_window_s"] = list(window)
        truth["sc_laps_by_driver"] = _overlapping_laps(build, window)
    if plan.yellow is not None:
        window = _flag_window(build, plan.yellow, yellow=True)
        truth["yellow_window_s"] = list(window)
        truth["yellow_laps_by_driver"] = _overlapping_laps(build, window)
    if plan.dirty_air is not None:
        driver, first, last = plan.dirty_air
        truth["dirty_air_laps"] = {driver: list(range(first, last + 1))}
    return truth


def _flag_window(
    build: SessionBuild, laps: tuple[int, int], *, yellow: bool = False
) -> tuple[float, float]:
    """The flag's own start/end offsets, mirroring `_emit_race_control`."""
    first, last = laps
    start = _leader_crossing(build, first - 1) + 1.0
    end = _last_crossing(build, last) + 1.0
    return (start, end)


def _overlapping_laps(build: SessionBuild, window: tuple[float, float]) -> dict[str, list[int]]:
    """Laps whose `(start, end]` window intersects `window`.

    The same interval predicate `f1_kernel.status.StatusInterval.overlaps`
    uses — shared as a DEFINITION of "ran under this flag", computed here
    from the simulation's own numbers.
    """
    start, end = window
    result: dict[str, list[int]] = {}
    for lap in build.laps:
        if end > lap.start_s and start < lap.end_s:
            result.setdefault(lap.driver, []).append(lap.lap_number)
    return {driver: sorted(laps) for driver, laps in sorted(result.items())}


if __name__ == "__main__":
    raise SystemExit(main())
