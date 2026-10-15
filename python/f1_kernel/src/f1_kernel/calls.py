"""The calls ledger and the race's scorecard (ROADMAP W5).

A *call* is what the desk said about a car the lap before it boxed:
- **undercut**: from `f1_kernel.undercut`, the window on the car directly
  ahead ("comes through if they stay out to lap N"). The live desk shows
  undercut calls only, so overcut rows of the backtest are not calls.
- **rejoin**: from `f1_kernel.rejoin`, where it comes out ("P6, behind
  RUS").

A call is *locked* when the car's stop makes it the one that counts, and
*resolved* when the race settles it: right, wrong, void (a comparison the
model cannot fairly be held to, with the reason), or no call (the inputs
were too thin to say anything, `window=unknown`). Only right and wrong
count on the scorecard, split by the confidence the call carried.

`call_ledger` is the backtests' own evaluators run over the laps released
so far (`final=False`), so the scorecard at the flag is the backtest on
that race, by construction. Each call is dated by the line crossings that
locked and settled it, so a stream can release it no earlier than the
viewer's clock reaches that moment.

Pure and deterministic, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import polars as pl

from f1_kernel.frames import SessionFrames
from f1_kernel.params import KernelParams
from f1_kernel.rejoin import PitLossEstimate, RejoinOutcome, evaluate_session_rejoins
from f1_kernel.undercut import Confidence
from f1_kernel.undercut_eval import StopPairOutcome, evaluate_session_stops


class CallKind(StrEnum):
    UNDERCUT = "undercut"
    REJOIN = "rejoin"


class CallStatus(StrEnum):
    PENDING = "pending"
    """Locked, outcome still to come."""
    RIGHT = "right"
    WRONG = "wrong"
    VOID = "void"
    """Not a fair test of the call; `detail` says why. Never scored."""
    NO_CALL = "no_call"
    """The model said nothing usable (`window=unknown`). Never scored."""


CLEARED_CALL_KINDS: frozenset[CallKind] = frozenset()
"""The call kinds the live desk may show and score: those whose committed
held-out 2025 backtest passes W5's ordering check (high beats medium beats
low). Empty until `just w5-holdout` produces passing reports.
`test_calls.py` fails if this ever disagrees with the reports in
`python/f1_kernel/reports/`, so clearing a kind takes a passing report and
this line together."""


@dataclass(frozen=True, slots=True)
class Call:
    """One call, locked and (eventually) resolved."""

    call_id: str
    """Stable across recomputation: kind, car, rival (undercut) and in-lap."""
    kind: CallKind
    driver: str
    """The car that boxed."""
    rival: str | None
    """Undercut: the car ahead it attacks. Rejoin: the car projected ahead
    after the stop (None: it would lead)."""
    decision_lap: int
    in_lap: int
    made_at_utc: datetime | None
    """The end of the decision lap: when the model had what it used."""
    locked_at_utc: datetime
    confidence: Confidence
    status: CallStatus
    detail: str | None
    """Why void, or what a pending call waits for."""
    resolved_at_utc: datetime | None
    # -- the call --------------------------------------------------------
    window: str | None
    crossover_lap: int | None
    """Undercut: it comes through if the rival stays out to this lap."""
    projected_position: int | None
    projected_margin_s: float | None
    """Rejoin: clear air to the nearest car at the rejoin point."""
    # -- what happened ---------------------------------------------------
    rival_stop_lap: int | None
    driver_ahead_after: bool | None
    """Undercut: whether the car that boxed was ahead once both had."""
    actual_position: int | None
    actual_car_ahead: str | None

    @property
    def counts(self) -> bool:
        return self.status in (CallStatus.RIGHT, CallStatus.WRONG)


@dataclass(frozen=True, slots=True)
class ScoreLine:
    confidence: Confidence
    right: int
    wrong: int


@dataclass(frozen=True, slots=True)
class Scorecard:
    """Resolved calls only. `by_confidence` runs high, medium, low."""

    right: int
    wrong: int
    by_confidence: tuple[ScoreLine, ...]
    pending: int
    void: int
    no_call: int


def call_ledger(
    frames: SessionFrames,
    params: KernelParams,
    *,
    total_laps: int | None = None,
    final: bool = True,
    pit_loss: PitLossEstimate | None = None,
    kinds: Sequence[CallKind] = (CallKind.UNDERCUT, CallKind.REJOIN),
) -> list[Call]:
    """Every call locked so far, oldest first.

    `frames` is the race as far as it has run (the laps released so far);
    `final=True` once it is over, which resolves whatever still waits on a
    rival who never stopped or a car that never finished the lap.
    `total_laps` is the race distance when known: the undercut evaluator
    reads it exactly as the backtest does.
    """
    laps = frames.laps
    if laps.is_empty():
        return []
    ends: dict[tuple[str, int], datetime] = {
        (str(row["driver"]), int(row["lap_number"])): row["lap_end_utc"]
        for row in laps.select("driver", "lap_number", "lap_end_utc")
        .filter(pl.col("lap_end_utc").is_not_null())
        .to_dicts()
    }
    race_end = max(ends.values()) if ends else None
    calls: list[Call] = []
    if CallKind.UNDERCUT in kinds:
        for outcome in evaluate_session_stops(frames, params, total_laps=total_laps, final=final):
            if outcome.kind == CallKind.UNDERCUT.value:
                call = _undercut_call(outcome, ends, race_end)
                if call is not None:
                    calls.append(call)
    if CallKind.REJOIN in kinds:
        for outcome in evaluate_session_rejoins(frames, params, pit_loss=pit_loss, final=final):
            call = _rejoin_call(outcome, ends, race_end)
            if call is not None:
                calls.append(call)
    return sorted(calls, key=lambda c: (c.locked_at_utc, c.call_id))


def scorecard(calls: Sequence[Call]) -> Scorecard:
    lines = tuple(
        ScoreLine(
            confidence=level,
            right=sum(1 for c in calls if c.confidence is level and c.status is CallStatus.RIGHT),
            wrong=sum(1 for c in calls if c.confidence is level and c.status is CallStatus.WRONG),
        )
        for level in (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW)
    )
    return Scorecard(
        right=sum(line.right for line in lines),
        wrong=sum(line.wrong for line in lines),
        by_confidence=lines,
        pending=sum(1 for c in calls if c.status is CallStatus.PENDING),
        void=sum(1 for c in calls if c.status is CallStatus.VOID),
        no_call=sum(1 for c in calls if c.status is CallStatus.NO_CALL),
    )


def _undercut_call(
    outcome: StopPairOutcome,
    ends: dict[tuple[str, int], datetime],
    race_end: datetime | None,
) -> Call | None:
    """None for an exclusion decided before the call was ever made (no
    prediction exists: a double-stacked rival, say)."""
    driver, rival = outcome.attacker, outcome.defender
    in_lap = outcome.attacker_stop_lap
    locked = ends.get((driver, in_lap + 1))
    if locked is None:
        return None  # only reachable for an excluded row with no out-lap
    resolved: datetime | None = None
    if outcome.pending:
        status = CallStatus.PENDING
    else:
        if not outcome.evaluated:
            status = CallStatus.VOID
        elif outcome.hit is None:
            status = CallStatus.NO_CALL
        else:
            status = CallStatus.RIGHT if outcome.hit else CallStatus.WRONG
        moment = outcome.settled_at_utc or race_end
        resolved = None if moment is None else max(moment, locked)
    return Call(
        call_id=f"undercut:{driver}:{rival}:{in_lap}",
        kind=CallKind.UNDERCUT,
        driver=driver,
        rival=rival,
        decision_lap=outcome.decision_lap,
        in_lap=in_lap,
        made_at_utc=ends.get((driver, outcome.decision_lap)),
        locked_at_utc=locked,
        confidence=Confidence(outcome.confidence) if outcome.confidence else Confidence.LOW,
        status=status,
        detail=outcome.exclusion,
        resolved_at_utc=resolved,
        window=outcome.window,
        crossover_lap=outcome.crossover_lap,
        projected_position=None,
        projected_margin_s=None,
        rival_stop_lap=outcome.defender_stop_lap,
        driver_ahead_after=outcome.actual_ahead,
        actual_position=None,
        actual_car_ahead=None,
    )


def _rejoin_call(
    outcome: RejoinOutcome,
    ends: dict[tuple[str, int], datetime],
    race_end: datetime | None,
) -> Call | None:
    locked = ends.get((outcome.driver, outcome.in_lap))
    if locked is None:
        return None
    projection = outcome.projection
    if outcome.pending:
        status, resolved = CallStatus.PENDING, None
    else:
        status = (
            CallStatus.VOID
            if not outcome.evaluated
            else CallStatus.RIGHT
            if outcome.hit
            else CallStatus.WRONG
        )
        resolved = outcome.settled_at_utc or race_end
        if resolved is not None:
            resolved = max(resolved, locked)
    return Call(
        call_id=f"rejoin:{outcome.driver}:{outcome.in_lap}",
        kind=CallKind.REJOIN,
        driver=outcome.driver,
        rival=projection.car_ahead,
        decision_lap=outcome.decision_lap,
        in_lap=outcome.in_lap,
        made_at_utc=ends.get((outcome.driver, outcome.decision_lap)),
        locked_at_utc=locked,
        confidence=projection.confidence,
        status=status,
        detail=outcome.exclusion,
        resolved_at_utc=resolved,
        window=None,
        crossover_lap=None,
        projected_position=projection.position,
        projected_margin_s=projection.margin_s,
        rival_stop_lap=None,
        driver_ahead_after=None,
        actual_position=outcome.actual_position,
        actual_car_ahead=outcome.actual_car_ahead,
    )


__all__ = [
    "CLEARED_CALL_KINDS",
    "Call",
    "CallKind",
    "CallStatus",
    "ScoreLine",
    "Scorecard",
    "call_ledger",
    "scorecard",
]
