"""A hand-built synthetic race with PLANTED anomalies of every kind, built
directly as polars frames (not via `DomainEvent`s/a state machine) so every
column is under exact, deterministic control — the same "checked-in
synthetic fixture with exact ground truth" layer `f1_kernel`'s own tests
use (`python/f1_kernel/tests/fixtures/`), just constructed inline since
this package needs no parquet-shaped historical corpus.

Six drivers, each isolated in its own widely-separated wall-clock time
band (`_CLUSTER_OFFSET_S`, thousands of seconds apart — far more than
`KernelParams.undercut_max_gap_s`) so `f1_kernel.undercut_eval`'s race-order
pairing never mixes one driver's story into another's:

- **"1"** — a lone 24-lap stint with a real (planted) quadratic
  degradation cliff, plus one lap-8 lock-up (the lap AND its sector 2
  spike hard; sector 1/3 stay untouched) — the
  PACE_LOSS/OFF_TRACK/DEG_CLIFF story.
- **"2"/"3"** — a green-flag duel: "2" runs 2s ahead, pits first (lap
  10/11) onto a compound with a large, HIDDEN pace advantage over "3", who
  stays out and pits later (lap 14/15) — the UNDERCUT_RESOLVED story
  (`f1_kernel.undercut`'s own model cannot see a compound's pace change in
  advance, so this is a genuine walk-forward test, not a rigged one).
- **"4"/"5"** — flat, boring pace with one clean on-track position swap at
  lap 15, neither ever pitting — the POSITION_CHANGE story.
- **"6"** — one pit stop with a deliberately blown-out cost — the
  PIT_ANOMALY story.

Every lap runs green with no gap-interval samples at all
(`gap_ahead_samples=0`), so the clean-lap filter's dirty-air check is
always "unknown", never a confound this fixture did not deliberately set
up. `event_name=None`/no circuit, so pit-loss comparisons resolve against
`KernelParams.default_pit_loss_s`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import polars as pl

from f1_domain import SessionKey, SessionType
from f1_kernel import STINT_FRAME_SCHEMA, SessionFrames
from f1_kernel.schemas import LAP_FRAME_SCHEMA

SESSION_KEY = SessionKey(year=2030, round=1, session_type=SessionType.RACE)
TOTAL_LAPS = 24
SESSION_START = datetime(2030, 1, 1, 13, 0, 0, tzinfo=UTC)

HERO = "1"
PACE_LOSS_LAP = 8
PACE_LOSS_SECTOR = 2
PACE_LOSS_SPIKE_S = 8.0

DUEL_LEADER = "2"
DUEL_CHASER = "3"
DUEL_GAP_S = 2.0
DUEL_MEDIUM_S = 90.0
DUEL_HARD_S = 80.0
DUEL_PIT_LOSS_S = 23.03  # matches KernelParams.default_pit_loss_s exactly
DUEL_LEADER_STOP_IN_LAP = 10
DUEL_CHASER_STOP_IN_LAP = 14

SWAP_A = "4"
SWAP_B = "5"
SWAP_LAP = 15

PIT_ANOMALY_DRIVER = "6"
PIT_ANOMALY_IN_LAP = 8
PIT_ANOMALY_EXCESS_S = 16.97  # (measured pit loss) - default_pit_loss_s

# Cluster offsets: each far enough apart (>> KernelParams.undercut_max_gap_s)
# that no driver from one story is ever "adjacent in race order" to another.
_CLUSTER_OFFSET_S: dict[str, float] = {
    "1": 0.0,
    "2": 2000.0,
    "3": 2000.0 + DUEL_GAP_S,
    "4": 4000.0,
    "5": 4001.0,
    "6": 6000.0,
}


@dataclass(frozen=True, slots=True)
class _LapPlan:
    """One driver's full lap-by-lap plan, in the shape the builder below
    turns into frame rows: a raw lap time per lap, which laps are pit
    in/out, what stint each lap belongs to, and a classification position
    per lap."""

    lap_times_s: list[float]
    pit_in_laps: frozenset[int]
    pit_out_laps: frozenset[int]
    stints: list[tuple[int, int, str, int]]  # (start_lap, end_lap, compound, tyre_age_at_start)
    positions: list[int]


def _flat_plan(lap_time_s: float, *, positions: int | Sequence[int] = 1) -> _LapPlan:
    resolved_positions = [positions] * TOTAL_LAPS if isinstance(positions, int) else list(positions)
    return _LapPlan(
        lap_times_s=[lap_time_s] * TOTAL_LAPS,
        pit_in_laps=frozenset(),
        pit_out_laps=frozenset(),
        stints=[(1, TOTAL_LAPS, "medium", 0)],
        positions=resolved_positions,
    )


def _hero_plan() -> _LapPlan:
    """A smooth, real quadratic degradation trend across the whole stint,
    plus one additive lock-up spike on `PACE_LOSS_LAP` that lands entirely
    in sector 2 (sector 1/3 stay exactly constant, a clean negative
    control for OFF_TRACK)."""
    lap_times = [0.0] * TOTAL_LAPS
    for n in range(1, TOTAL_LAPS + 1):
        base = 90.0 + 0.02 * n + 0.02 * n * n
        lap_times[n - 1] = base + (PACE_LOSS_SPIKE_S if n == PACE_LOSS_LAP else 0.0)
    return _LapPlan(
        lap_times_s=lap_times,
        pit_in_laps=frozenset(),
        pit_out_laps=frozenset(),
        stints=[(1, TOTAL_LAPS, "medium", 0)],
        positions=[5] * TOTAL_LAPS,
    )


def _duel_plan(*, stop_in_lap: int, position: int) -> _LapPlan:
    """Flat medium pace, a stop costing exactly the calibrated norm
    (`DUEL_PIT_LOSS_S`), then flat (much faster) hard pace — a real
    pace-changing stop `f1_kernel.undercut`'s own model cannot foresee."""
    stop_out_lap = stop_in_lap + 1
    lap_times = [
        DUEL_MEDIUM_S if n <= stop_in_lap else DUEL_HARD_S for n in range(1, TOTAL_LAPS + 1)
    ]
    lap_times[stop_out_lap - 1] = DUEL_HARD_S + DUEL_PIT_LOSS_S  # the out-lap absorbs the cost
    return _LapPlan(
        lap_times_s=lap_times,
        pit_in_laps=frozenset({stop_in_lap}),
        pit_out_laps=frozenset({stop_out_lap}),
        stints=[
            (1, stop_in_lap, "medium", 0),
            (stop_out_lap, TOTAL_LAPS, "hard", 0),
        ],
        positions=[position] * TOTAL_LAPS,
    )


def _swap_plan(lap_time_s: float, *, before: int, after: int) -> _LapPlan:
    positions = [before] * (SWAP_LAP - 1) + [after] * (TOTAL_LAPS - SWAP_LAP + 1)
    return _flat_plan(lap_time_s, positions=positions)


def _pit_anomaly_plan() -> _LapPlan:
    """Flat pace either side of one stop whose out-lap absorbs a cost far
    beyond `KernelParams.default_pit_loss_s`."""
    stop_in_lap = PIT_ANOMALY_IN_LAP
    stop_out_lap = stop_in_lap + 1
    pre_pace = 92.0
    post_pace = 91.0
    excess = PIT_ANOMALY_EXCESS_S + DUEL_PIT_LOSS_S  # the raw excess ON TOP of the calibrated norm
    lap_times = [pre_pace if n <= stop_in_lap else post_pace for n in range(1, TOTAL_LAPS + 1)]
    lap_times[stop_out_lap - 1] = post_pace + excess
    return _LapPlan(
        lap_times_s=lap_times,
        pit_in_laps=frozenset({stop_in_lap}),
        pit_out_laps=frozenset({stop_out_lap}),
        stints=[
            (1, stop_in_lap, "medium", 0),
            (stop_out_lap, TOTAL_LAPS, "hard", 0),
        ],
        positions=[6] * TOTAL_LAPS,
    )


_PLANS: dict[str, _LapPlan] = {
    HERO: _hero_plan(),
    DUEL_LEADER: _duel_plan(stop_in_lap=DUEL_LEADER_STOP_IN_LAP, position=1),
    DUEL_CHASER: _duel_plan(stop_in_lap=DUEL_CHASER_STOP_IN_LAP, position=2),
    SWAP_A: _swap_plan(91.0, before=3, after=4),
    SWAP_B: _swap_plan(91.5, before=4, after=3),
    PIT_ANOMALY_DRIVER: _pit_anomaly_plan(),
}


def _stint_for_lap(plan: _LapPlan, lap_number: int) -> tuple[str, int]:
    for index, (start, end, compound, _tyre_age_at_start) in enumerate(plan.stints, start=1):
        if start <= lap_number <= end:
            return compound, index
    raise AssertionError(f"lap {lap_number} not covered by any planned stint")


def _tyre_age(plan: _LapPlan, lap_number: int) -> int:
    for start, end, _compound, tyre_age_at_start in plan.stints:
        if start <= lap_number <= end:
            return lap_number - start + 1 + tyre_age_at_start
    raise AssertionError(f"lap {lap_number} not covered by any planned stint")


def _build_lap_rows(driver: str, plan: _LapPlan) -> list[dict[str, object]]:
    offset = _CLUSTER_OFFSET_S[driver]
    rows: list[dict[str, object]] = []
    elapsed = 0.0
    for lap_number in range(1, TOTAL_LAPS + 1):
        lap_time = plan.lap_times_s[lap_number - 1]
        start = SESSION_START + timedelta(seconds=offset + elapsed)
        elapsed += lap_time
        end = SESSION_START + timedelta(seconds=offset + elapsed)
        compound, stint_number = _stint_for_lap(plan, lap_number)
        sector1 = 28.0
        sector3 = 28.0
        sector2 = lap_time - sector1 - sector3
        assert sector2 >= 0.0, f"{driver} lap {lap_number}: sector2 went negative"
        rows.append(
            {
                "session_year": SESSION_KEY.year,
                "session_round": SESSION_KEY.round,
                "session_type": SESSION_KEY.session_type.value,
                "driver": driver,
                "lap_number": lap_number,
                "lap_start_utc": start,
                "lap_end_utc": end,
                "lap_time_s": lap_time,
                "sector1_s": sector1,
                "sector2_s": sector2,
                "sector3_s": sector3,
                "is_personal_best": False,
                "compound": compound,
                "tyre_age": _tyre_age(plan, lap_number),
                "stint_number": stint_number,
                "position": plan.positions[lap_number - 1],
                "gap_ahead_s": None,
                "gap_ahead_min_s": None,
                "gap_ahead_samples": 0,
                "gap_to_leader_s": None,
                "track_status": "green",
                "track_status_flags": [],
                "is_pit_in": lap_number in plan.pit_in_laps,
                "is_pit_out": lap_number in plan.pit_out_laps,
                "provenance": "live",
            }
        )
    return rows


def _build_stint_rows(driver: str, plan: _LapPlan) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, (start, end, compound, tyre_age_at_start) in enumerate(plan.stints, start=1):
        rows.append(
            {
                "session_year": SESSION_KEY.year,
                "session_round": SESSION_KEY.round,
                "session_type": SESSION_KEY.session_type.value,
                "driver": driver,
                "stint_number": index,
                "compound": compound,
                "start_lap": start,
                "end_lap": end,
                "lap_count": end - start + 1,
                "tyre_age_at_start": tyre_age_at_start,
                "provenance": "live",
            }
        )
    return rows


def build_session_frames() -> SessionFrames:
    """The full six-driver synthetic race as a `SessionFrames`, ready to
    pass straight into `f1_kernel.analyze_session_from_frames` and then
    `f1_why.detect_anomalies`."""
    lap_rows: list[dict[str, object]] = []
    stint_rows: list[dict[str, object]] = []
    for driver, plan in _PLANS.items():
        lap_rows.extend(_build_lap_rows(driver, plan))
        stint_rows.extend(_build_stint_rows(driver, plan))

    laps = pl.DataFrame(lap_rows, schema=LAP_FRAME_SCHEMA).sort("driver", "lap_number")
    stints = pl.DataFrame(stint_rows, schema=STINT_FRAME_SCHEMA).sort("driver", "stint_number")
    return SessionFrames(session_key=SESSION_KEY, laps=laps, stints=stints, event_name=None)
