"""The undercut backtest: predict at the lap BEFORE the real stop, using
only data available up to that lap, then score against what happened.

This is the milestone-defining test for `f1_kernel.undercut` (CLAUDE.md:
"backtested against replays, not mocks"). For every real green-flag stop
in a race it reconstructs the duel the pit wall saw:

- **Undercut attempts** — the stopping car had a rival directly ahead
  within `params.undercut_max_gap_s` who stopped LATER. At decision lap
  `in_lap - 1`, with the lap frame sliced to that lap and degradation
  refitted on the slice (no future information — the walk-forward
  contract), `undercut_window` makes its call; the realized outcome is
  whether the attacker was ahead once both cars had completed their
  out-laps.
- **Overcut attempts** — the mirror: the stopping car had a rival directly
  BEHIND who stayed out and stopped later. `overcut_window` is asked, at
  the same decision lap, whether staying out to the rival's realized stop
  lap keeps the place.

The prediction scored is the full decision boundary, not a coin flip:
`crossover_lap` says from which defender response the move comes through,
and the realized response lap picks the side. A hit means the boundary was
on the right side of reality.

Pairs the green-flag model cannot honestly claim are EXCLUDED and counted,
never silently dropped: stops under SC/VSC/red, double-stacked responses
(no stagger), rivals who never stopped again (no "after both stopped"
truth exists), sequences interrupted by a neutralization, and pairs whose
outcome lap nobody completed. The exclusion ledger is part of the result —
a backtest that quietly cherry-picks its stops proves nothing.

`evaluate_session_stops` is a pure `(SessionFrames, KernelParams) -> rows`
analytic, so `f1_kernel.Backtest` runs it over a corpus unchanged;
`tools/backtest_undercut.py` is the committed-report entry point.

**The same function is the live ledger** (ROADMAP W5, `f1_kernel.calls`).
Over the laps released so far, `final=False` reports a pair that is still
waiting (the rival has not stopped, or the outcome lap is not run) as
`pending` rather than excluding it, with the call already filled in. Once
the race is over (`final=True`) the ledger IS the backtest: the live
scorecard cannot drift from the published hit rates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from f1_domain import TrackStatusKind
from f1_kernel.cleaning import clean_laps
from f1_kernel.degradation import DegradationFit, fit_degradation
from f1_kernel.frames import SessionFrames
from f1_kernel.fuel import fuel_correct
from f1_kernel.params import KernelParams
from f1_kernel.undercut import (
    CrossoverKind,
    CrossoverResult,
    WindowState,
    overcut_window,
    undercut_window,
)

_NEUTRALIZED = frozenset(
    {TrackStatusKind.SC.value, TrackStatusKind.VSC.value, TrackStatusKind.RED.value}
)
"""Statuses that void a green-flag stop or a green-flag duel window. A
local yellow does not — excluding every lap that brushed a yellow would
gut the sample without changing the physics of a stop."""


@dataclass(frozen=True, slots=True)
class StopPairOutcome:
    """One real stop-pair: the prediction made at the decision lap and the
    outcome the race delivered. `evaluated=False` rows are the exclusion
    ledger — kept so the backtest can report what it did NOT score."""

    kind: str
    attacker: str
    defender: str
    decision_lap: int
    attacker_stop_lap: int
    defender_stop_lap: int | None
    gap_at_decision_s: float | None
    evaluated: bool
    exclusion: str | None
    predicted_ahead: bool | None
    """The scored call: attacker ahead after both stops. None when the
    window came back UNKNOWN — an honest no-call, reported separately from
    a wrong call."""
    predicted_margin_s: float | None
    """Projected margin at the REALIZED second stop lap (positive =
    attacker ahead) — comparable directly against `actual_margin_s`."""
    projected_gap_if_covered_s: float | None
    crossover_lap: int | None
    window: str | None
    opens_in_laps: int | None
    confidence: str | None
    actual_ahead: bool | None
    actual_margin_s: float | None
    truth_lap: int | None
    hit: bool | None
    """predicted_ahead == actual_ahead; None when either side is None."""
    reasons: str
    pending: bool = False
    """Only with `final=False`: the call is made but its outcome has not
    happened yet; `exclusion` says what it waits for. Never in a backtest."""
    evidence_lap: int | None = None
    """The lap whose end settled this row (the outcome lap, the rival's
    out-lap, the first neutralised lap). None when only the end of the race
    settles it."""
    settled_at_utc: datetime | None = None
    """The crossing that settled it: the later car's at the outcome lap, the
    rival's out-lap, the first neutralised crossing. Laps arrive in time
    order, so this never moves once set. None while pending, and when only
    the end of the race settles it. `f1_kernel.calls` dates a resolution by it."""
    fit_floor: str | None = None
    """The worse of the two degradation fits' reliability — where
    `confidence` starts before any cap. With the three fields below, the
    breakdown of WHY a call got its confidence (ROADMAP W5): the backtest
    reports hit rate along each, so a confidence rule that orders badly
    can be traced to the input that misleads it."""
    covered_band_crosses_zero: bool | None = None
    """The immediate-cover margin changes sign across the deg-rate 95%
    CIs — the check that caps `confidence` at medium today."""
    scored_band_crosses_zero: bool | None = None
    """The same check at the rival's REAL stop lap, the margin the call is
    actually scored on. Reported only; never feeds `confidence`."""
    decision_not_green: bool | None = None
    """Either car's decision lap ran under a non-green status — the check
    that caps `confidence` at low."""


def evaluate_session_stops(
    frames: SessionFrames,
    params: KernelParams,
    *,
    total_laps: int | None = None,
    final: bool = True,
) -> list[StopPairOutcome]:
    """Every scoreable stop-pair in one race, walked forward honestly.

    Pure and deterministic. Fits are computed once per distinct decision
    lap (sliced to it) and shared by the pairs that decide there.

    `total_laps` is the race distance; it defaults to the last lap in the
    frame, right for a finished race. `final=False` reads the frame as a
    race still running (see the module docstring).
    """
    laps = frames.laps
    if laps.is_empty():
        return []
    if total_laps is None:
        found = laps["lap_number"].max()
        if not isinstance(found, int):
            return []
        total_laps = found

    rows_by_driver_lap: dict[tuple[str, int], dict[str, object]] = {
        (str(row["driver"]), int(row["lap_number"])): row for row in laps.to_dicts()
    }
    stops_by_driver: dict[str, list[int]] = {}
    for (driver, lap_number), row in sorted(rows_by_driver_lap.items()):
        if row["is_pit_in"]:
            stops_by_driver.setdefault(driver, []).append(lap_number)

    fits_cache: dict[int, tuple[SessionFrames, list[DegradationFit]]] = {}

    def fits_at(decision_lap: int) -> tuple[SessionFrames, list[DegradationFit]]:
        if decision_lap not in fits_cache:
            sliced = laps.filter(pl.col("lap_number") <= decision_lap)
            sliced_state = SessionFrames(
                session_key=frames.session_key,
                laps=sliced,
                stints=frames.stints.filter(pl.col("start_lap") <= decision_lap)
                if not frames.stints.is_empty()
                else frames.stints,
                circuit=frames.circuit,
                event_name=frames.event_name,
            )
            fits = fit_degradation(
                fuel_correct(clean_laps(sliced, params), params, total_laps=total_laps),
                params,
            )
            fits_cache[decision_lap] = (sliced_state, fits)
        return fits_cache[decision_lap]

    outcomes: list[StopPairOutcome | None] = []
    for driver, stop_laps in sorted(stops_by_driver.items()):
        for stop_lap in stop_laps:
            if stop_lap < 2:
                continue  # a lap-1 stop has no decision lap to predict from
            if not _is_green_stop(rows_by_driver_lap, driver, stop_lap):
                continue
            decision_lap = stop_lap - 1
            order = _race_order_at(laps, decision_lap)
            if driver not in order:
                continue
            index = order.index(driver)

            if index > 0:
                outcomes.append(
                    _score_pair(
                        CrossoverKind.UNDERCUT,
                        attacker=driver,
                        defender=order[index - 1],
                        first_stop_lap=stop_lap,
                        rows=rows_by_driver_lap,
                        stops_by_driver=stops_by_driver,
                        fits_at=fits_at,
                        params=params,
                        total_laps=total_laps,
                        final=final,
                    )
                )
            if index + 1 < len(order):
                outcomes.append(
                    _score_pair(
                        CrossoverKind.OVERCUT,
                        attacker=order[index + 1],
                        defender=driver,
                        first_stop_lap=stop_lap,
                        rows=rows_by_driver_lap,
                        stops_by_driver=stops_by_driver,
                        fits_at=fits_at,
                        params=params,
                        total_laps=total_laps,
                        final=final,
                    )
                )

    return [outcome for outcome in outcomes if outcome is not None]


def evaluate_session_stops_rows(
    frames: SessionFrames, params: KernelParams
) -> list[Mapping[str, object]]:
    """`evaluate_session_stops` as plain rows — the `f1_kernel.Backtest`
    analytic adapter (`bt.run(evaluate_session_stops_rows, races)`)."""
    return [
        {
            "kind": o.kind,
            "attacker": o.attacker,
            "defender": o.defender,
            "decision_lap": o.decision_lap,
            "attacker_stop_lap": o.attacker_stop_lap,
            "defender_stop_lap": o.defender_stop_lap,
            "gap_at_decision_s": o.gap_at_decision_s,
            "evaluated": o.evaluated,
            "exclusion": o.exclusion,
            "predicted_ahead": o.predicted_ahead,
            "predicted_margin_s": o.predicted_margin_s,
            "projected_gap_if_covered_s": o.projected_gap_if_covered_s,
            "crossover_lap": o.crossover_lap,
            "window": o.window,
            "opens_in_laps": o.opens_in_laps,
            "confidence": o.confidence,
            "actual_ahead": o.actual_ahead,
            "actual_margin_s": o.actual_margin_s,
            "truth_lap": o.truth_lap,
            "hit": o.hit,
            "reasons": o.reasons,
            "fit_floor": o.fit_floor,
            "covered_band_crosses_zero": o.covered_band_crosses_zero,
            "scored_band_crosses_zero": o.scored_band_crosses_zero,
            "decision_not_green": o.decision_not_green,
            "pending": o.pending,
            "evidence_lap": o.evidence_lap,
        }
        for o in evaluate_session_stops(frames, params)
    ]


# ---------------------------------------------------------------------------
# pair mechanics
# ---------------------------------------------------------------------------


def _score_pair(
    kind: CrossoverKind,
    *,
    attacker: str,
    defender: str,
    first_stop_lap: int,
    rows: dict[tuple[str, int], dict[str, object]],
    stops_by_driver: dict[str, list[int]],
    fits_at: Callable[[int], tuple[SessionFrames, list[DegradationFit]]],
    params: KernelParams,
    total_laps: int,
    final: bool = True,
) -> StopPairOutcome | None:
    """One discovered pair, scored or excluded with a reason. The FIRST
    stopper is the attacker for an undercut and the defender for an
    overcut; `stayer` is the other car, whose realized stop closes the
    book. With `final=False` a pair whose outcome has not happened yet
    comes back `pending`, its call already made."""
    decision_lap = first_stop_lap - 1
    stayer = defender if kind is CrossoverKind.UNDERCUT else attacker

    gap = _gap_between(rows, attacker, defender, decision_lap)
    if gap is None or gap < 0 or gap > params.undercut_max_gap_s:
        return None  # not a duel: unknown gap, or out of undercut range

    def excluded(
        reason: str,
        stayer_stop: int | None = None,
        evidence_lap: int | None = None,
        settled_at: datetime | None = None,
    ) -> StopPairOutcome:
        """Not scored, but the call was made: it carries it, so the live
        ledger shows what a voided call said."""
        result, _ = _predict(
            kind, attacker, defender, decision_lap, None, fits_at, params, total_laps
        )
        return _outcome(
            kind,
            attacker,
            defender,
            decision_lap,
            first_stop_lap,
            stayer_stop,
            gap,
            result,
            rows,
            predicted_ahead=None,
            truth=None,
            exclusion=reason,
            evidence_lap=evidence_lap,
            settled_at=settled_at,
        )

    pair = (attacker, defender)

    def waiting(reason: str, stayer_stop: int | None = None) -> StopPairOutcome:
        """Still running: the call as made, its outcome to come."""
        result, _ = _predict(
            kind, attacker, defender, decision_lap, None, fits_at, params, total_laps
        )
        return _outcome(
            kind,
            attacker,
            defender,
            decision_lap,
            first_stop_lap,
            stayer_stop,
            gap,
            result,
            rows,
            predicted_ahead=None,
            truth=None,
            exclusion=reason,
            pending=True,
        )

    if decision_lap in stops_by_driver.get(stayer, []):
        # The rival pitted at the end of the decision lap itself, a lap
        # before this car: it stopped first, so this was never the duel
        # called. Known the moment the call locks.
        return excluded(
            "rival_stopped_first",
            decision_lap,
            first_stop_lap + 1,
            _end(
                rows, attacker if kind is CrossoverKind.UNDERCUT else defender, first_stop_lap + 1
            ),
        )
    stayer_stop = next((lap for lap in stops_by_driver.get(stayer, []) if lap > decision_lap), None)
    if stayer_stop is not None and stayer_stop <= first_stop_lap:
        return excluded(
            "double_stacked_or_simultaneous",
            stayer_stop,
            first_stop_lap + 1,
            _end(
                rows, attacker if kind is CrossoverKind.UNDERCUT else defender, first_stop_lap + 1
            ),
        )

    # Voids the laps run so far already settle, the earliest-known first. A
    # fact once true stays true, so a void's reason never changes as the
    # race goes on, and the finished race gives the same one: the live
    # ledger never re-tells a call it has settled.
    truth_lap = stayer_stop + 1 if stayer_stop is not None else None
    latest = max((lap for (driver, lap) in rows if driver in pair), default=decision_lap)
    voids: list[tuple[datetime, int, str]] = []
    neutral = _first_neutralized(
        rows, pair, decision_lap + 1, truth_lap if truth_lap is not None else latest
    )
    if neutral is not None:
        voids.append((neutral[0], neutral[1], "neutralized_between_stops"))
    out_end = _end(rows, stayer, stayer_stop + 1) if stayer_stop is not None else None
    if (
        stayer_stop is not None
        and out_end is not None
        and not _is_green_stop(rows, stayer, stayer_stop)
    ):
        voids.append((out_end, stayer_stop + 1, "rival_stop_not_green"))
    if voids:
        settled_at, evidence_lap, reason = min(voids)
        return excluded(reason, stayer_stop, evidence_lap, settled_at)

    if stayer_stop is None:
        if final:
            return excluded("rival_never_stopped_again")
        return waiting("waiting_for_rival_stop")
    if (stayer, stayer_stop + 1) not in rows:
        if final:  # no out-lap: it never left the pits
            return excluded("rival_stop_not_green", stayer_stop)
        return waiting("waiting_for_rival_out_lap", stayer_stop)

    assert truth_lap is not None
    actual_margin = _gap_between(rows, defender, attacker, truth_lap)
    if actual_margin is None:
        if not final and any((driver, truth_lap) not in rows for driver in pair):
            return waiting("waiting_for_outcome_lap", stayer_stop)
        return excluded("no_common_outcome_lap", stayer_stop)
    # _gap_between(defender, attacker, ...) is defender-behind-attacker:
    # positive means the attacker crossed the line first, i.e. is ahead.

    result, predicted_ahead = _predict(
        kind, attacker, defender, decision_lap, stayer_stop, fits_at, params, total_laps
    )
    return _outcome(
        kind,
        attacker,
        defender,
        decision_lap,
        first_stop_lap,
        stayer_stop,
        gap,
        result,
        rows,
        predicted_ahead=predicted_ahead,
        truth=(truth_lap, actual_margin),
    )


def _predict(
    kind: CrossoverKind,
    attacker: str,
    defender: str,
    decision_lap: int,
    stayer_stop: int | None,
    fits_at: Callable[[int], tuple[SessionFrames, list[DegradationFit]]],
    params: KernelParams,
    total_laps: int,
) -> tuple[CrossoverResult, bool | None]:
    """The call made at the decision lap, from the slice to it. Its window,
    crossover lap and confidence never depend on `stayer_stop`; only the
    scored side does, once the rival's real stop is known."""
    sliced_state, fits = fits_at(decision_lap)
    if kind is CrossoverKind.UNDERCUT:
        result = undercut_window(
            sliced_state,
            attacker,
            defender,
            fits,
            params,
            decision_lap=decision_lap,
            total_laps=total_laps,
            assume_defender_stop_lap=stayer_stop,
        )
        if stayer_stop is None or result.window is WindowState.UNKNOWN:
            return result, None
        return result, result.crossover_lap is not None and stayer_stop >= result.crossover_lap
    result = overcut_window(
        sliced_state,
        attacker,
        defender,
        fits,
        params,
        decision_lap=decision_lap,
        total_laps=total_laps,
        assume_attacker_stop_lap=stayer_stop,
    )
    if stayer_stop is None or result.window is WindowState.UNKNOWN:
        return result, None
    return result, result.crossover_lap is not None and stayer_stop <= result.crossover_lap


def _outcome(
    kind: CrossoverKind,
    attacker: str,
    defender: str,
    decision_lap: int,
    first_stop_lap: int,
    stayer_stop: int | None,
    gap: float,
    result: CrossoverResult,
    rows: dict[tuple[str, int], dict[str, object]],
    *,
    predicted_ahead: bool | None,
    truth: tuple[int, float] | None,
    exclusion: str | None = None,
    pending: bool = False,
    evidence_lap: int | None = None,
    settled_at: datetime | None = None,
) -> StopPairOutcome:
    undercut = kind is CrossoverKind.UNDERCUT
    truth_lap, actual_margin = truth if truth is not None else (None, None)
    actual_ahead = None if actual_margin is None else actual_margin > 0
    return StopPairOutcome(
        kind=kind.value,
        attacker=attacker,
        defender=defender,
        decision_lap=decision_lap,
        attacker_stop_lap=first_stop_lap if undercut else (stayer_stop or 0),
        defender_stop_lap=stayer_stop if undercut else first_stop_lap,
        gap_at_decision_s=gap,
        evaluated=truth is not None,
        exclusion=exclusion,
        predicted_ahead=predicted_ahead,
        predicted_margin_s=result.projected_margin_at_assumed_stop_s,
        projected_gap_if_covered_s=result.projected_gap_if_covered_s,
        crossover_lap=result.crossover_lap,
        window=result.window.value,
        opens_in_laps=result.opens_in_laps,
        confidence=result.confidence.value,
        actual_ahead=actual_ahead,
        actual_margin_s=actual_margin,
        truth_lap=truth_lap,
        hit=None
        if predicted_ahead is None or actual_ahead is None
        else predicted_ahead == actual_ahead,
        reasons="; ".join(result.reasons),
        fit_floor=_fit_floor(result),
        covered_band_crosses_zero=_crosses_zero(
            result.projected_gap_if_covered_low_s, result.projected_gap_if_covered_high_s
        ),
        scored_band_crosses_zero=_crosses_zero(
            result.projected_margin_at_assumed_stop_low_s,
            result.projected_margin_at_assumed_stop_high_s,
        ),
        decision_not_green=any(
            _non_green(rows.get((driver, decision_lap))) for driver in (attacker, defender)
        ),
        pending=pending,
        evidence_lap=truth_lap if truth is not None else evidence_lap,
        settled_at_utc=_settled_by_outcome(rows, (attacker, defender), truth_lap)
        if truth_lap is not None
        else settled_at,
    )


_RELIABILITY_ORDER = ("unreliable", "thin", "good")


def _fit_floor(result: CrossoverResult) -> str | None:
    found = [
        state.fit_reliability
        for state in (result.attacker_state, result.defender_state)
        if state.fit_reliability in _RELIABILITY_ORDER
    ]
    if len(found) < 2:
        return None
    return min(found, key=_RELIABILITY_ORDER.index)


def _crosses_zero(low: float | None, high: float | None) -> bool | None:
    if low is None or high is None:
        return None
    return min(low, high) < 0 < max(low, high)


def _non_green(row: dict[str, object] | None) -> bool:
    """`f1_kernel.undercut`'s own reading of a decision lap's status."""
    if row is None:
        return False
    return row.get("track_status") not in (
        None,
        TrackStatusKind.GREEN.value,
        TrackStatusKind.CHEQUERED.value,
    )


def _is_green_stop(
    rows: dict[tuple[str, int], dict[str, object]], driver: str, in_lap: int
) -> bool:
    """A routine tyre stop the green-flag model can speak to: an out-lap
    follows, the stint changes, and neither lap ran under SC/VSC/red."""
    in_row = rows.get((driver, in_lap))
    out_row = rows.get((driver, in_lap + 1))
    if in_row is None or out_row is None or not out_row["is_pit_out"]:
        return False
    if out_row["is_pit_in"]:
        return False
    in_stint, out_stint = in_row["stint_number"], out_row["stint_number"]
    if in_stint is None or out_stint is None or in_stint == out_stint:
        return False
    return not (_row_neutralized(in_row) or _row_neutralized(out_row))


def _row_neutralized(row: dict[str, object]) -> bool:
    flags = row.get("track_status_flags")
    if flags is None:
        return False
    return any(flag in _NEUTRALIZED for flag in flags)  # type: ignore[union-attr]


def _first_neutralized(
    rows: dict[tuple[str, int], dict[str, object]],
    drivers: tuple[str, str],
    from_lap: int,
    to_lap: int,
) -> tuple[datetime, int] | None:
    """The earliest crossing in the lap range at which either car had run
    under SC/VSC/red, and its lap."""
    found = [
        (end, lap_number)
        for lap_number in range(from_lap, to_lap + 1)
        for driver in drivers
        if (row := rows.get((driver, lap_number))) is not None
        and _row_neutralized(row)
        and isinstance(end := row.get("lap_end_utc"), datetime)
    ]
    return min(found) if found else None


def _end(rows: dict[tuple[str, int], dict[str, object]], driver: str, lap: int) -> datetime | None:
    row = rows.get((driver, lap))
    end = row.get("lap_end_utc") if row is not None else None
    return end if isinstance(end, datetime) else None


def _settled_by_outcome(
    rows: dict[tuple[str, int], dict[str, object]], pair: tuple[str, str], truth_lap: int
) -> datetime | None:
    ends = [end for driver in pair if (end := _end(rows, driver, truth_lap)) is not None]
    return max(ends) if ends else None


def _race_order_at(laps: pl.DataFrame, lap_number: int) -> list[str]:
    """Drivers who completed `lap_number`, in the order they crossed the
    line — race order among cars on the same lap. Cars a lap down appear
    ~a lap time adrift and fall out of any pairing via the gap cap."""
    at_lap = laps.filter((pl.col("lap_number") == lap_number) & pl.col("lap_end_utc").is_not_null())
    return [str(d) for d in at_lap.sort("lap_end_utc")["driver"].to_list()]


def _gap_between(
    rows: dict[tuple[str, int], dict[str, object]],
    behind: str,
    ahead: str,
    lap_number: int,
) -> float | None:
    """Seconds `behind` crossed the line after `ahead` at the end of
    `lap_number`; negative when 'behind' was actually in front. None when
    either car did not complete the lap."""
    behind_row = rows.get((behind, lap_number))
    ahead_row = rows.get((ahead, lap_number))
    if behind_row is None or ahead_row is None:
        return None
    behind_end = behind_row.get("lap_end_utc")
    ahead_end = ahead_row.get("lap_end_utc")
    if not isinstance(behind_end, datetime) or not isinstance(ahead_end, datetime):
        return None
    return (behind_end - ahead_end).total_seconds()


__all__ = [
    "StopPairOutcome",
    "evaluate_session_stops",
    "evaluate_session_stops_rows",
]
