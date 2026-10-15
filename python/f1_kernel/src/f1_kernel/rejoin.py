"""Where a car comes out if it pits now — and, after the stop, whether it
did (ROADMAP W5: "Box now and HAM rejoins P6, in traffic behind RUS").

**The projection** (`project_rejoin`, `rejoin_snapshot`). At the end of a
decision lap every car's line-crossing time is known. A car that boxes at
the end of the NEXT lap loses one pit stop, `pit_loss_s`, against everyone
who stays out. Pit loss is measured (`f1_kernel.pit_loss`) as the in-lap
plus out-lap against the car's own clean neighbouring laps, so it already
holds the pit lane, the stationary time and the out-lap warm-up; the
projection adds it to the car's crossing time and reads off who is then
ahead and behind. Pace differences over those two laps are not modelled:
the same assumption the measurement made.

**Confidence** comes from how close the nearest car would be against the
measured spread of ONE stop at this circuit (`PitLossEstimate.sigma_s`,
recovered exactly from the circuit file's standard error and stop count).
At least `params.rejoin_high_margin_sigmas` of clear air on both sides is
`high`, at least `params.rejoin_medium_margin_sigmas` is `medium`, closer
is `low`. A circuit whose pit loss is not calibrated has no measured
spread, and a decision lap not run green is not representative: both are
`low`, with the reason said.

**The outcome** (`evaluate_session_rejoins`). For every real green-flag
stop, the call made at the lap before the in-lap is scored at the end of
the out-lap: right when the car directly ahead in race order is the one
projected. Void, and counted, when the comparison is not fair: another
car around the rejoin point pitted on the same laps, or the projected car
ahead never finished the out-lap. The same `final=False` reading as
`f1_kernel.undercut_eval` makes it the live ledger.

Pure and deterministic, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from f1_domain import TrackStatusKind
from f1_kernel.frames import SessionFrames
from f1_kernel.params import KernelParams
from f1_kernel.pit_loss import MEDIAN_SE_FACTOR
from f1_kernel.undercut import Confidence

_NEUTRALIZED = frozenset(
    {TrackStatusKind.SC.value, TrackStatusKind.VSC.value, TrackStatusKind.RED.value}
)


@dataclass(frozen=True, slots=True)
class PitLossEstimate:
    """What one stop costs at a circuit, and how much one stop varies."""

    value_s: float
    sigma_s: float | None
    """The spread of a single stop around `value_s` (1.4826 x MAD), None
    when the value is not measured (provisional, default, override)."""
    provenance: str


def pit_loss_estimate(state: SessionFrames, params: KernelParams) -> PitLossEstimate:
    """The circuit's pit loss with its single-stop spread, under the same
    precedence `f1_kernel.undercut` resolves pit loss by."""
    circuit = state.circuit
    if circuit is None:
        return PitLossEstimate(params.default_pit_loss_s, None, "default")
    if circuit.circuit_id in params.pit_loss_overrides_s:
        return PitLossEstimate(params.pit_loss_overrides_s[circuit.circuit_id], None, "override")
    sigma: float | None = None
    if (
        circuit.pit_loss_provenance == "calibrated"
        and circuit.pit_loss_uncertainty_s is not None
        and circuit.pit_loss_n_stops
    ):
        # The file holds the standard error of the median,
        # MEDIAN_SE_FACTOR * sigma / sqrt(n); undo it.
        sigma = circuit.pit_loss_uncertainty_s * math.sqrt(circuit.pit_loss_n_stops)
        sigma /= MEDIAN_SE_FACTOR
    return PitLossEstimate(circuit.pit_loss_s, sigma, circuit.pit_loss_provenance)


@dataclass(frozen=True, slots=True)
class RejoinProjection:
    """One car's "if it boxes at the end of the next lap" projection."""

    driver: str
    decision_lap: int
    """Timing through the end of this lap only."""
    in_lap: int
    """`decision_lap + 1`: the lap the projection assumes it boxes at the end of."""
    position_now: int
    position: int
    """Race position at the end of the out-lap, if nobody else stops."""
    car_ahead: str | None
    """The car directly ahead after the stop; None when it would lead."""
    gap_to_ahead_s: float | None
    car_behind: str | None
    gap_to_behind_s: float | None
    margin_s: float | None
    """The smaller of the two gaps: how much the stop can cost more or less
    than usual before the order changes. None when nobody is around."""
    pit_loss_s: float
    pit_loss_provenance: str
    pit_loss_sigma_s: float | None
    confidence: Confidence
    reasons: tuple[str, ...]


def project_rejoin(
    state: SessionFrames,
    driver: str,
    decision_lap: int,
    params: KernelParams,
    *,
    pit_loss: PitLossEstimate | None = None,
) -> RejoinProjection | None:
    """The projection for one car; None when it has not completed the
    decision lap."""
    ends = _crossings(state.laps, decision_lap)
    if driver not in ends:
        return None
    estimate = pit_loss if pit_loss is not None else pit_loss_estimate(state, params)
    status = _status_at(state.laps, driver, decision_lap)
    return _project(driver, decision_lap, ends, estimate, status, params)


def rejoin_snapshot(
    state: SessionFrames,
    params: KernelParams,
    *,
    decision_lap: int,
    pit_loss: PitLossEstimate | None = None,
) -> list[RejoinProjection]:
    """Every car that completed the decision lap, in race order, except
    one that pitted on it (in the pits now) or ran its out-lap on it (just
    stopped): "if it boxes next lap" says nothing about either. They still
    count as cars on track for everyone else's projection."""
    ends = _crossings(state.laps, decision_lap)
    estimate = pit_loss if pit_loss is not None else pit_loss_estimate(state, params)
    at = state.laps.filter(pl.col("lap_number") == decision_lap)
    stopping = {
        str(driver)
        for driver in at.filter(pl.col("is_pit_in") | pl.col("is_pit_out"))["driver"].to_list()
    }
    return [
        _project(
            driver,
            decision_lap,
            ends,
            estimate,
            _status_at(state.laps, driver, decision_lap),
            params,
        )
        for driver in sorted(ends, key=ends.__getitem__)
        if driver not in stopping
    ]


def _crossings(laps: pl.DataFrame, lap_number: int) -> dict[str, datetime]:
    at = laps.filter((pl.col("lap_number") == lap_number) & pl.col("lap_end_utc").is_not_null())
    return {
        str(row["driver"]): row["lap_end_utc"]
        for row in at.select("driver", "lap_end_utc").to_dicts()
    }


def _status_at(laps: pl.DataFrame, driver: str, lap_number: int) -> str | None:
    row = laps.filter((pl.col("driver") == driver) & (pl.col("lap_number") == lap_number))
    if row.is_empty():
        return None
    value = row["track_status"][0]
    return None if value is None else str(value)


def _project(
    driver: str,
    decision_lap: int,
    ends: Mapping[str, datetime],
    estimate: PitLossEstimate,
    status: str | None,
    params: KernelParams,
) -> RejoinProjection:
    mine = ends[driver]
    # Seconds each other car crossed after this one (negative: ahead).
    relative = {
        other: (end - mine).total_seconds() for other, end in ends.items() if other != driver
    }
    loss = estimate.value_s
    ahead = [(rel, other) for other, rel in relative.items() if rel < loss]
    behind = [(rel, other) for other, rel in relative.items() if rel >= loss]
    car_ahead = max(ahead)[1] if ahead else None
    car_behind = min(behind)[1] if behind else None
    gap_ahead = loss - relative[car_ahead] if car_ahead is not None else None
    gap_behind = relative[car_behind] - loss if car_behind is not None else None
    gaps = [gap for gap in (gap_ahead, gap_behind) if gap is not None]
    margin = min(gaps) if gaps else None

    reasons: list[str] = [
        "lapped cars and pace over the in-lap and out-lap are not modelled",
    ]
    if estimate.sigma_s is None:
        confidence = Confidence.LOW
        reasons.append(
            f"pit loss here is {estimate.provenance}, not measured: no spread to judge "
            "how close a call this is"
        )
    elif margin is None:
        confidence = Confidence.HIGH
        reasons.append("no other car near the rejoin point")
    elif margin >= params.rejoin_high_margin_sigmas * estimate.sigma_s:
        confidence = Confidence.HIGH
    elif margin >= params.rejoin_medium_margin_sigmas * estimate.sigma_s:
        confidence = Confidence.MEDIUM
        reasons.append(
            f"{margin:.1f} s from the nearest car, within "
            f"{params.rejoin_high_margin_sigmas:g} x the {estimate.sigma_s:.1f} s "
            "spread of one stop"
        )
    else:
        confidence = Confidence.LOW
        reasons.append(
            f"{margin:.1f} s from the nearest car, inside the {estimate.sigma_s:.1f} s spread "
            "of one stop: either side is plausible"
        )
    if status not in (None, TrackStatusKind.GREEN.value, TrackStatusKind.CHEQUERED.value):
        confidence = Confidence.LOW
        reasons.append(f"track not green at the decision lap (status: {status})")

    return RejoinProjection(
        driver=driver,
        decision_lap=decision_lap,
        in_lap=decision_lap + 1,
        position_now=1 + sum(1 for rel in relative.values() if rel < 0),
        position=1 + len(ahead),
        car_ahead=car_ahead,
        gap_to_ahead_s=gap_ahead,
        car_behind=car_behind,
        gap_to_behind_s=gap_behind,
        margin_s=margin,
        pit_loss_s=loss,
        pit_loss_provenance=estimate.provenance,
        pit_loss_sigma_s=estimate.sigma_s,
        confidence=confidence,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# the outcome: walk-forward, scored at the end of the out-lap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejoinOutcome:
    """One real green-flag stop: the projection made at the lap before the
    in-lap, and where the car actually came out."""

    driver: str
    decision_lap: int
    in_lap: int
    evaluated: bool
    exclusion: str | None
    """Why it is not scored (void), or with `pending`, what it waits for."""
    pending: bool
    projection: RejoinProjection
    actual_position: int | None
    actual_car_ahead: str | None
    hit: bool | None
    settled_at_utc: datetime | None
    """When the last fact it needed crossed the line: the car's out-lap
    end, or a later neighbour's. None while pending, and for a void the
    end of the race settles."""


def evaluate_session_rejoins(
    frames: SessionFrames,
    params: KernelParams,
    *,
    pit_loss: PitLossEstimate | None = None,
    final: bool = True,
) -> list[RejoinOutcome]:
    """Every real green-flag stop's rejoin call, scored. Pure."""
    laps = frames.laps
    if laps.is_empty():
        return []
    rows: dict[tuple[str, int], dict[str, object]] = {
        (str(row["driver"]), int(row["lap_number"])): row for row in laps.to_dicts()
    }
    estimate = pit_loss if pit_loss is not None else pit_loss_estimate(frames, params)
    outcomes: list[RejoinOutcome] = []
    for (driver, in_lap), row in sorted(rows.items()):
        if not row["is_pit_in"] or in_lap < 2:
            continue
        decision_lap = in_lap - 1
        ends = _crossings(laps, decision_lap)
        if driver not in ends:
            continue
        if _neutral(row):
            continue  # a stop under SC/VSC/red: a different model, never called
        projection = _project(
            driver,
            decision_lap,
            ends,
            estimate,
            _status_of(rows.get((driver, decision_lap))),
            params,
        )
        out_row = rows.get((driver, in_lap + 1))
        if out_row is None and final:
            # Locked when it pitted, so it stays in the ledger: void.
            outcomes.append(_void(driver, in_lap, projection, "retired_in_the_pits", None))
        elif out_row is not None and not _is_green_stop(row, out_row):
            end = out_row.get("lap_end_utc")
            outcomes.append(
                _void(
                    driver,
                    in_lap,
                    projection,
                    "stop_not_green",
                    end if isinstance(end, datetime) else None,
                )
            )
        else:
            outcomes.append(_score(driver, in_lap, projection, rows, final=final))
    return outcomes


def _void(
    driver: str,
    in_lap: int,
    projection: RejoinProjection,
    reason: str,
    settled_at: datetime | None,
) -> RejoinOutcome:
    return RejoinOutcome(
        driver=driver,
        decision_lap=projection.decision_lap,
        in_lap=in_lap,
        evaluated=False,
        exclusion=reason,
        pending=False,
        projection=projection,
        actual_position=None,
        actual_car_ahead=None,
        hit=None,
        settled_at_utc=settled_at,
    )


def _score(
    driver: str,
    in_lap: int,
    projection: RejoinProjection,
    rows: dict[tuple[str, int], dict[str, object]],
    *,
    final: bool,
) -> RejoinOutcome:
    out_lap = in_lap + 1

    settled: list[datetime] = []

    def result(
        *,
        exclusion: str | None = None,
        pending: bool = False,
        actual: tuple[int, str | None] | None = None,
        at_race_end: bool = False,
    ) -> RejoinOutcome:
        hit = None if actual is None else actual[1] == projection.car_ahead
        settled_at = None if pending or at_race_end or not settled else max(settled)
        return RejoinOutcome(
            driver=driver,
            decision_lap=projection.decision_lap,
            in_lap=in_lap,
            evaluated=actual is not None,
            exclusion=exclusion,
            pending=pending,
            projection=projection,
            actual_position=None if actual is None else actual[0],
            actual_car_ahead=None if actual is None else actual[1],
            hit=hit,
            settled_at_utc=settled_at,
        )

    if (driver, out_lap) not in rows:
        return result(exclusion="waiting_for_out_lap", pending=True)

    # Cars whose order against this one the stop could change: those it
    # drops behind, and the projected neighbours. If any of them pitted
    # around the same laps, the projected order was never on offer.
    decision = projection.decision_lap
    mine = rows[(driver, decision)]["lap_end_utc"]
    assert isinstance(mine, datetime)
    relative = {
        other: (end - mine).total_seconds()
        for (other, lap), row in rows.items()
        if lap == decision
        and other != driver
        and isinstance(end := row.get("lap_end_utc"), datetime)
    }
    involved = {other for other, rel in relative.items() if 0 < rel < projection.pit_loss_s}
    involved |= {car for car in (projection.car_ahead, projection.car_behind) if car is not None}
    # Each of them must have finished the out-lap (or the race be over)
    # before anything is settled: one that pits at the end of it crosses
    # after this car, and would turn a scored call void.
    if not final and any((other, out_lap) not in rows for other in involved):
        return result(exclusion="waiting_for_cars_around", pending=True)
    for other in (driver, *involved):
        end = rows.get((other, out_lap), {}).get("lap_end_utc")
        if isinstance(end, datetime):
            settled.append(end)
    for other in sorted(involved):
        for lap in (decision, in_lap, out_lap):
            other_row = rows.get((other, lap))
            if other_row is not None and other_row["is_pit_in"]:
                return result(exclusion="another_car_pitted_around_the_rejoin")

    if projection.car_ahead is not None and (projection.car_ahead, out_lap) not in rows:
        # Only once the race is over (it is one of `involved`): it never
        # finished the lap, so whether it would have stayed ahead is moot.
        return result(exclusion="projected_car_ahead_did_not_finish_the_lap", at_race_end=True)

    finished = {
        other: end
        for (other, lap), row in rows.items()
        if lap == out_lap and isinstance(end := row.get("lap_end_utc"), datetime)
    }
    mine_out = finished[driver]
    before = sorted(
        (end, other) for other, end in finished.items() if other != driver and end < mine_out
    )
    car_ahead = before[-1][1] if before else None
    return result(actual=(1 + len(before), car_ahead))


def _status_of(row: dict[str, object] | None) -> str | None:
    if row is None:
        return None
    value = row.get("track_status")
    return None if value is None else str(value)


def _neutral(row: dict[str, object]) -> bool:
    flags = row.get("track_status_flags")
    if flags is None:
        return False
    return any(flag in _NEUTRALIZED for flag in flags)  # type: ignore[union-attr]


def _is_green_stop(in_row: dict[str, object], out_row: dict[str, object]) -> bool:
    """`f1_kernel.undercut_eval`'s routine-stop test."""
    if not out_row["is_pit_out"] or out_row["is_pit_in"]:
        return False
    in_stint, out_stint = in_row["stint_number"], out_row["stint_number"]
    if in_stint is None or out_stint is None or in_stint == out_stint:
        return False
    return not (_neutral(in_row) or _neutral(out_row))


__all__ = [
    "PitLossEstimate",
    "RejoinOutcome",
    "RejoinProjection",
    "evaluate_session_rejoins",
    "pit_loss_estimate",
    "project_rejoin",
    "rejoin_snapshot",
]
