"""Undercut / overcut crossover prediction: what is ABOUT to happen.

Every public dashboard shows the gap; none of them answer the question a
pit wall actually asks: *if we pit now, do we come out ahead of the car in
front once they have stopped too?* This module answers it as a pure
function over the same fitted state the rest of the kernel derives —
per-stint degradation fits (`f1_kernel.degradation`), fuel-corrected pace,
the measured per-circuit pit loss (`f1_kernel.pit_loss`) — and returns a
structured `CrossoverResult` with the honesty signals CLAUDE.md requires:
a confidence derived from the underlying fit reliability, and an explicit
UNKNOWN when the inputs cannot support a call.

## The model

Both cars are projected with their CURRENT stint's fitted linear pace:
`pace(age) = intercept + rate * age` in fuel-corrected time (fuel
correction shifts both cars on the same lap by the same amount, so every
pairwise margin below is exactly what raw times would give). A stop resets
a car to `age = 1` on the out-lap (the frame's tyre-age convention) at the
same fitted intercept — "your fresh-tyre pace is what this stint's fit
extrapolates to at low age" — optionally shifted by
`KernelParams.compound_offset_s` when the attacker changes compound.

**Undercut** (`undercut_window`): attacker A sits `gap` behind defender B.
A pits with in-lap `decision_lap + 1`. For each lap `n` the defender could
respond on (in-lap `n >= decision_lap + 2` — B cannot react before seeing
A stop), project both cars' cumulative time through the end of B's out-lap
and report the margin. `crossover_lap` is the earliest `n` that leaves A
ahead; the realized duel then reduces to *did B stay out that long?*

**Overcut** (`overcut_window`): the mirror. B (ahead) pits with in-lap
`decision_lap + 1`; A stays out banking laps on worn tyres against B's
fresh pace. `crossover_lap` is the LATEST in-lap A can take and still
emerge ahead — a deadline, not an opening.

## Two properties worth understanding before trusting a number

- **Equal pit losses cancel.** Both cars pay the same green-flag pit loss
  once, so it drops out of every "after both have stopped" margin — the
  undercut comes through on the PACE DIFFERENCE over the stop stagger
  (fresh attacker laps against the defender's worn ones), not on the size
  of the pit loss. The calibrated pit loss still matters: it sets the
  projected REJOIN gap (`projected_rejoin_gap_s` — who you come out
  behind, i.e. traffic exposure), and it is the quantity a future
  safety-car-asymmetry model would change for one car only.
- **Green-flag conditions are assumed throughout.** A safety car during
  the sequence rewrites the arithmetic entirely (that asymmetry is why the
  pit-loss calibration is green-flag-only), and rejoin traffic is not
  modelled — the projection is clean-air. The confidence field says when
  the decision lap itself was not green.

The walk-forward honesty contract: `deg_fits` must be fitted on laps up to
`decision_lap` ONLY. `undercut_snapshot` (the per-lap live primitive — one
call per completed lap gives Phase 3 its countdown) and the backtest
harness (`f1_kernel.undercut_eval`) both guarantee it; if you call
`undercut_window` directly with end-of-race fits, you are measuring
hindsight, not prediction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

import polars as pl

from f1_domain import Compound, TrackStatusKind
from f1_kernel.cleaning import clean_laps
from f1_kernel.degradation import DegradationFit, Reliability, fit_degradation
from f1_kernel.frames import SessionFrames
from f1_kernel.fuel import fuel_correct
from f1_kernel.params import KernelParams
from f1_kernel.schemas import Schema

FIRST_COVER_STAGGER_LAPS = 2
"""The earliest the second car can stop after the decision lap: the second
stopper's in-lap is the lap AFTER the first stopper's (they react to a stop
they have seen), and the first stopper's in-lap is `decision_lap + 1`."""


class CrossoverKind(StrEnum):
    UNDERCUT = "undercut"
    """The attacker (behind) stops first and attacks on fresh tyres."""

    OVERCUT = "overcut"
    """The defender (ahead) stops first; the attacker stays out."""


class WindowState(StrEnum):
    """The headline a UI renders — 'open now / opens in N laps / closed'."""

    OPEN = "open"
    """Undercut: pitting now wins even if the defender covers on the very
    next lap. Overcut: staying out at least one lap keeps the place."""

    OPENS_LATER = "opens_later"
    """Undercut only: pitting now loses to an immediate cover, but if both
    cars keep running as fitted, pitting in `opens_in_laps` laps wins."""

    CLOSED = "closed"
    """The move does not come through anywhere inside the horizon."""

    UNKNOWN = "unknown"
    """The inputs cannot support a call (no fit, no gap, no tyre age).
    `reasons` says exactly what was missing — never a silent guess."""


class Confidence(StrEnum):
    """How much to trust this result — derived from the underlying
    degradation-fit reliability, never asserted independently of it."""

    HIGH = "high"
    """Both fits `good`, and the decision margin keeps its sign across the
    fits' own 95% confidence intervals."""

    MEDIUM = "medium"
    """A `thin` fit behind one side, or a margin inside fit uncertainty."""

    LOW = "low"
    """An `unreliable` fit, a non-green decision lap, or (with UNKNOWN)
    inputs too thin to project. A LOW call is a hypothesis, not advice."""


_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}

_RELIABILITY_RANK: dict[Reliability, int] = {
    Reliability.UNRELIABLE: 0,
    Reliability.THIN: 1,
    Reliability.GOOD: 2,
}


@dataclass(frozen=True, slots=True)
class DriverStintState:
    """One driver's fitted state at the decision lap — the evidence behind
    a projection, carried on the result so no number is anonymous."""

    driver: str
    stint_number: int | None
    compound: str | None
    tyre_age: int | None
    deg_rate_s_per_lap: float | None
    deg_rate_ci_low_s_per_lap: float | None
    deg_rate_ci_high_s_per_lap: float | None
    intercept_s: float | None
    pace_now_s: float | None
    """Fitted fuel-corrected pace at the decision lap's tyre age."""
    n_laps_in_fit: int | None
    fit_reliability: str | None
    """`f1_kernel.degradation.Reliability` value, None when no fit."""


@dataclass(frozen=True, slots=True)
class CrossoverResult:
    """One (attacker, defender) crossover projection. Structured results
    only — rendering is Phase 3's job."""

    kind: CrossoverKind
    attacker: str
    """The driver trying to take the place — always the car BEHIND."""
    defender: str
    """The car ahead, defending the place."""
    decision_lap: int
    """The projection uses data through the END of this lap only. 0 when
    no common lap existed (window is UNKNOWN then)."""
    attacker_stop_lap: int | None
    """The attacker's assumed in-lap when the scenario fixes it (undercut:
    `decision_lap + 1`). None when it is the scanned variable (overcut)."""
    defender_stop_lap: int | None
    """The defender's assumed in-lap when the scenario fixes it (overcut:
    `decision_lap + 1`). None when it is the scanned variable (undercut)."""
    gap_s: float | None
    """On-track gap at the decision lap, seconds; positive = attacker
    behind. From lap-completion timestamps unless overridden."""
    pit_loss_s: float
    pit_loss_provenance: str
    """'override' (argument or params override), 'calibrated'/'provisional'
    (the circuit file's own marker), or 'default' (no circuit metadata)."""
    out_lap_penalty_s: float
    crossover_lap: int | None
    """Undercut: the EARLIEST defender in-lap that leaves the attacker
    ahead once the defender has stopped — 'the undercut comes through if
    they stay out to lap N'. Overcut: the LATEST attacker in-lap that
    still keeps the attacker ahead — 'you must stop by lap N'. None: the
    move does not come through inside the horizon (or UNKNOWN)."""
    projected_gap_at_crossover_s: float | None
    """Margin at `crossover_lap`, seconds; positive = attacker ahead."""
    projected_gap_if_covered_s: float | None
    """The conservative headline: margin when the second car stops at the
    earliest possible lap (undercut: defender covers immediately; overcut:
    attacker responds immediately). Positive = attacker ahead."""
    projected_gap_if_covered_low_s: float | None
    projected_gap_if_covered_high_s: float | None
    """`projected_gap_if_covered_s` re-evaluated at the deg-rate 95% CI
    extremes (attacker slow + defender fast, and the reverse) — the band
    the confidence sign-stability check reads."""
    projected_margin_at_assumed_stop_s: float | None
    """Margin at the `assume_*_stop_lap` what-if, when one was passed —
    how the backtest scores a prediction against the realized stop."""
    projected_margin_at_assumed_stop_low_s: float | None
    projected_margin_at_assumed_stop_high_s: float | None
    """`projected_margin_at_assumed_stop_s` at the same deg-rate 95% CI
    extremes as the covered band. Reported, not used for `confidence`:
    the backtest compares the two bands (ROADMAP W5) before any rule
    moves to the lap a call is actually scored at."""
    projected_rejoin_gap_s: float | None
    """Physical gap the first stopper faces to the stayer right after the
    out-lap, seconds; positive = first stopper behind. The one number the
    (equal) pit loss and out-lap penalty DO move — traffic exposure."""
    window: WindowState
    opens_in_laps: int | None
    """0 when OPEN; for OPENS_LATER, how many laps of status quo until
    pitting beats an immediate cover; None when CLOSED/UNKNOWN and for
    overcuts (a deadline has nothing to open)."""
    horizon_lap: int
    """Last second-stop in-lap the projection considered: `decision_lap +
    params.undercut_horizon_laps`, capped by the race end when known."""
    confidence: Confidence
    reasons: tuple[str, ...]
    """Everything that shaped the window/confidence, human-readable."""
    attacker_state: DriverStintState
    defender_state: DriverStintState


# ---------------------------------------------------------------------------
# the projection core
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Pace:
    """A driver's linear fuel-corrected pace model at the decision lap."""

    intercept_s: float
    rate_s_per_lap: float
    age_at_decision: int

    def staying_out(self, k: int) -> float:
        """Projected time of the k-th lap after the decision lap (k >= 1),
        staying out on the current tyres."""
        return self.intercept_s + self.rate_s_per_lap * (self.age_at_decision + k)

    def fresh(self, j: int, compound_delta_s: float = 0.0) -> float:
        """Projected time of the j-th lap of a new stint (j = 1 is the
        out-lap, matching the frame's tyre-age convention)."""
        return self.intercept_s + compound_delta_s + self.rate_s_per_lap * j

    def aged(self, extra_laps: int) -> _Pace:
        """The same model observed `extra_laps` later — how the window
        scan advances both cars without refitting."""
        return _Pace(self.intercept_s, self.rate_s_per_lap, self.age_at_decision + extra_laps)

    def with_rate(self, rate: float) -> _Pace:
        return _Pace(self.intercept_s, rate, self.age_at_decision)


def _stagger_delta(
    first: _Pace, stayer: _Pace, stagger_laps: int, first_compound_delta_s: float
) -> float:
    """Cumulative time of the first stopper MINUS the stayer, from the lap
    after the decision lap through the end of the stayer's out-lap, when
    the stayer's in-lap trails the decision lap by `stagger_laps` (>= 2).

    Both stops pay the same pit loss and out-lap penalty, so neither
    appears here — they cancel exactly (see the module docstring). What is
    left is the crossover's real engine: `stagger_laps` fresh laps for the
    first stopper against the same laps on the stayer's worn tyres.
    """
    total = first.staying_out(1) - stayer.fresh(1)
    for lap in range(1, stagger_laps + 1):
        total += first.fresh(lap, first_compound_delta_s) - stayer.staying_out(lap)
    return total


def _margin_fn(
    kind: CrossoverKind,
    attacker: _Pace,
    defender: _Pace,
    gap_s: float,
    compound_delta_s: float,
    decision_lap: int,
) -> Callable[[int], float]:
    """Margin (positive = attacker ahead) once both cars have stopped, as
    a function of the SECOND stop's absolute in-lap number."""
    if kind is CrossoverKind.UNDERCUT:

        def margin(second_stop_lap: int) -> float:
            stagger = second_stop_lap - decision_lap
            return -gap_s - _stagger_delta(attacker, defender, stagger, compound_delta_s)

    else:
        # The defender stops first; their fresh compound is unknowable, so
        # no compound delta is applied to them.
        def margin(second_stop_lap: int) -> float:
            stagger = second_stop_lap - decision_lap
            return _stagger_delta(defender, attacker, stagger, 0.0) - gap_s

    return margin


def _rejoin_gap(
    first: _Pace,
    stayer: _Pace,
    gap_first_behind_s: float,
    pit_loss_s: float,
    out_lap_penalty_s: float,
    first_compound_delta_s: float,
) -> float:
    """Physical gap of the first stopper behind the stayer at the end of
    the first stopper's out-lap (the stayer has not stopped). This is
    where the pit loss genuinely lands."""
    first_two_laps = (
        first.staying_out(1)
        + first.fresh(1, first_compound_delta_s)
        + pit_loss_s
        + out_lap_penalty_s
    )
    stayer_two_laps = stayer.staying_out(1) + stayer.staying_out(2)
    return gap_first_behind_s + first_two_laps - stayer_two_laps


# ---------------------------------------------------------------------------
# input resolution
# ---------------------------------------------------------------------------


def _lap_row(laps: pl.DataFrame, driver: str, lap_number: int) -> dict[str, object] | None:
    rows = laps.filter((pl.col("driver") == driver) & (pl.col("lap_number") == lap_number))
    if rows.is_empty():
        return None
    return rows.row(0, named=True)


def _fit_for(
    deg_fits: Sequence[DegradationFit], driver: str, stint_number: int | None
) -> DegradationFit | None:
    """The fit for a driver's current stint; when several match (a compound
    relabel mid-stint), the one with the most laps behind it."""
    if stint_number is None:
        return None
    matching = [
        fit for fit in deg_fits if fit.driver == driver and fit.stint_number == stint_number
    ]
    if not matching:
        return None
    return max(matching, key=lambda fit: fit.n_laps_used)


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _driver_state(
    row: dict[str, object] | None, driver: str, fit: DegradationFit | None
) -> DriverStintState:
    stint = _as_int(row.get("stint_number")) if row else None
    compound = row.get("compound") if row else None
    age = _as_int(row.get("tyre_age")) if row else None
    pace_now: float | None = None
    if fit is not None and age is not None:
        pace_now = fit.intercept_s + fit.deg_rate_s_per_lap * age
    return DriverStintState(
        driver=driver,
        stint_number=stint,
        compound=str(compound) if compound is not None else None,
        tyre_age=age,
        deg_rate_s_per_lap=fit.deg_rate_s_per_lap if fit else None,
        deg_rate_ci_low_s_per_lap=fit.deg_rate_ci_low_s_per_lap if fit else None,
        deg_rate_ci_high_s_per_lap=fit.deg_rate_ci_high_s_per_lap if fit else None,
        intercept_s=fit.intercept_s if fit else None,
        pace_now_s=pace_now,
        n_laps_in_fit=fit.n_laps_used if fit else None,
        fit_reliability=fit.reliability.value if fit else None,
    )


def _resolve_pit_loss(
    state: SessionFrames, params: KernelParams, override_s: float | None
) -> tuple[float, str]:
    if override_s is not None:
        return override_s, "override"
    circuit = state.circuit
    if circuit is not None:
        if circuit.circuit_id in params.pit_loss_overrides_s:
            return params.pit_loss_overrides_s[circuit.circuit_id], "override"
        return circuit.pit_loss_s, circuit.pit_loss_provenance
    return params.default_pit_loss_s, "default"


def _gap_from_rows(
    attacker_row: dict[str, object] | None, defender_row: dict[str, object] | None
) -> float | None:
    """On-track gap from lap-completion timestamps: how much later the
    attacker crossed the line at the end of the decision lap."""
    if attacker_row is None or defender_row is None:
        return None
    attacker_end = attacker_row.get("lap_end_utc")
    defender_end = defender_row.get("lap_end_utc")
    if not isinstance(attacker_end, datetime) or not isinstance(defender_end, datetime):
        return None
    return (attacker_end - defender_end).total_seconds()


def _non_green_status(row: dict[str, object] | None) -> str | None:
    if row is None:
        return None
    status = row.get("track_status")
    if status in (None, TrackStatusKind.GREEN.value, TrackStatusKind.CHEQUERED.value):
        return None
    return str(status)


def _latest_common_lap(laps: pl.DataFrame, driver_a: str, driver_b: str) -> int | None:
    common = (
        laps.filter(pl.col("driver").is_in([driver_a, driver_b]))
        .group_by("lap_number")
        .agg(pl.col("driver").n_unique().alias("n"))
        .filter(pl.col("n") == 2)
    )
    if common.is_empty():
        return None
    return _as_int(common["lap_number"].max())


def _reliability_confidence(attacker: DegradationFit, defender: DegradationFit) -> Confidence:
    worst = min((attacker.reliability, defender.reliability), key=_RELIABILITY_RANK.__getitem__)
    if worst is Reliability.GOOD:
        return Confidence.HIGH
    if worst is Reliability.THIN:
        return Confidence.MEDIUM
    return Confidence.LOW


def _cap(confidence: Confidence, ceiling: Confidence) -> Confidence:
    if _CONFIDENCE_RANK[confidence] <= _CONFIDENCE_RANK[ceiling]:
        return confidence
    return ceiling


def _compound_delta(
    params: KernelParams, current_compound: str | None, new_compound: Compound | str | None
) -> float:
    """Baseline shift of the attacker's fresh stint versus their current
    compound. Zero when staying on (or not stating) the same compound."""
    if new_compound is None:
        return 0.0
    return params.compound_offset(new_compound) - params.compound_offset(current_compound)


# ---------------------------------------------------------------------------
# the public analytics
# ---------------------------------------------------------------------------


def undercut_window(
    state: SessionFrames,
    driver_a: str,
    driver_b: str,
    deg_fits: Sequence[DegradationFit],
    params: KernelParams,
    *,
    decision_lap: int | None = None,
    total_laps: int | None = None,
    gap_s: float | None = None,
    pit_loss_s: float | None = None,
    attacker_new_compound: Compound | str | None = None,
    assume_defender_stop_lap: int | None = None,
) -> CrossoverResult:
    """Does pitting NOW put `driver_a` (behind) ahead of `driver_b` once
    the defender has eventually stopped — and if so, from which defender
    stop lap onward?

    The attacker's in-lap is `decision_lap + 1` ("box, box" on the lap in
    progress); the defender's response is scanned from `decision_lap + 2`
    to the horizon. See the module docstring for the model, the sign
    conventions (`gap_s` positive = attacker behind; margins positive =
    attacker ahead) and the walk-forward contract on `deg_fits`.

    `decision_lap` defaults to the latest lap both drivers have completed
    (the live "now"). `total_laps` caps the horizon at the race end when
    known — pass the scheduled distance live; the backtest passes the real
    one. `gap_s` overrides the timestamp-derived gap (a live caller may
    trust the interval channel more). `attacker_new_compound` shifts the
    attacker's fresh pace by the compound-offset delta; the defender is
    always projected onto their current compound (their plan is
    unknowable). `assume_defender_stop_lap` additionally evaluates the
    margin at one concrete response lap (a what-if; how the backtest
    scores against the realized stop).

    Pure and deterministic; O(horizon) arithmetic per call — cheap enough
    to recompute for every pair after every lap.
    """
    return _crossover(
        CrossoverKind.UNDERCUT,
        state,
        attacker=driver_a,
        defender=driver_b,
        deg_fits=deg_fits,
        params=params,
        decision_lap=decision_lap,
        total_laps=total_laps,
        gap_override_s=gap_s,
        pit_loss_override_s=pit_loss_s,
        attacker_new_compound=attacker_new_compound,
        assumed_second_stop_lap=assume_defender_stop_lap,
    )


def overcut_window(
    state: SessionFrames,
    driver_a: str,
    driver_b: str,
    deg_fits: Sequence[DegradationFit],
    params: KernelParams,
    *,
    decision_lap: int | None = None,
    total_laps: int | None = None,
    gap_s: float | None = None,
    pit_loss_s: float | None = None,
    attacker_new_compound: Compound | str | None = None,
    assume_attacker_stop_lap: int | None = None,
) -> CrossoverResult:
    """If `driver_b` (ahead) pits NOW and `driver_a` stays out, until which
    lap does the attacker's track position survive their own eventual stop?

    The mirror of `undercut_window`: the defender's in-lap is fixed at
    `decision_lap + 1`, the attacker's is scanned. `crossover_lap` is a
    DEADLINE — the latest in-lap that still works; `window` is OPEN when
    even stopping at the first opportunity (`decision_lap + 2`) keeps the
    place, CLOSED when nothing does. `opens_in_laps` is always None (a
    deadline has nothing to open). The attacker banks worn-tyre laps
    against the defender's fresh pace, so an OPEN overcut typically closes
    as the stagger grows — `crossover_lap` says when.
    """
    return _crossover(
        CrossoverKind.OVERCUT,
        state,
        attacker=driver_a,
        defender=driver_b,
        deg_fits=deg_fits,
        params=params,
        decision_lap=decision_lap,
        total_laps=total_laps,
        gap_override_s=gap_s,
        pit_loss_override_s=pit_loss_s,
        attacker_new_compound=attacker_new_compound,
        assumed_second_stop_lap=assume_attacker_stop_lap,
    )


def _crossover(
    kind: CrossoverKind,
    state: SessionFrames,
    *,
    attacker: str,
    defender: str,
    deg_fits: Sequence[DegradationFit],
    params: KernelParams,
    decision_lap: int | None,
    total_laps: int | None,
    gap_override_s: float | None,
    pit_loss_override_s: float | None,
    attacker_new_compound: Compound | str | None,
    assumed_second_stop_lap: int | None,
) -> CrossoverResult:
    laps = state.laps
    reasons: list[str] = []

    pit_loss, pit_loss_provenance = _resolve_pit_loss(state, params, pit_loss_override_s)
    if pit_loss_provenance == "provisional":
        reasons.append(
            "pit loss is provisional for this circuit — margins are unaffected "
            "(equal losses cancel) but the rejoin gap is order-of-magnitude"
        )
    elif pit_loss_provenance == "default":
        reasons.append("no circuit metadata — using KernelParams.default_pit_loss_s")

    resolved_decision = (
        decision_lap if decision_lap is not None else _latest_common_lap(laps, attacker, defender)
    )
    if resolved_decision is None:
        return _unknown_result(
            kind,
            attacker,
            defender,
            decision_lap=0,
            pit_loss=pit_loss,
            pit_loss_provenance=pit_loss_provenance,
            params=params,
            reasons=[*reasons, "no lap both drivers have completed — nothing to project from"],
            attacker_state=_driver_state(None, attacker, None),
            defender_state=_driver_state(None, defender, None),
            gap=None,
        )

    attacker_row = _lap_row(laps, attacker, resolved_decision)
    defender_row = _lap_row(laps, defender, resolved_decision)
    attacker_stint = _as_int(attacker_row.get("stint_number")) if attacker_row else None
    defender_stint = _as_int(defender_row.get("stint_number")) if defender_row else None
    attacker_fit = _fit_for(deg_fits, attacker, attacker_stint)
    defender_fit = _fit_for(deg_fits, defender, defender_stint)
    attacker_state = _driver_state(attacker_row, attacker, attacker_fit)
    defender_state = _driver_state(defender_row, defender, defender_fit)
    gap = (
        gap_override_s if gap_override_s is not None else _gap_from_rows(attacker_row, defender_row)
    )

    for driver, row, driver_state_, fit in (
        (attacker, attacker_row, attacker_state, attacker_fit),
        (defender, defender_row, defender_state, defender_fit),
    ):
        if row is None:
            reasons.append(f"no completed lap for {driver} at lap {resolved_decision}")
            continue
        if driver_state_.tyre_age is None:
            reasons.append(f"missing tyre age for {driver} at lap {resolved_decision}")
        if fit is None:
            reasons.append(
                f"no degradation fit for {driver}'s current stint at lap {resolved_decision}"
            )
        if (
            gap is None
            and gap_override_s is None
            and not isinstance(row.get("lap_end_utc"), datetime)
        ):
            reasons.append(f"gap unknown: missing lap-completion timestamp for {driver}")

    if (
        attacker_fit is None
        or defender_fit is None
        or attacker_state.tyre_age is None
        or defender_state.tyre_age is None
        or gap is None
    ):
        return _unknown_result(
            kind,
            attacker,
            defender,
            decision_lap=resolved_decision,
            pit_loss=pit_loss,
            pit_loss_provenance=pit_loss_provenance,
            params=params,
            reasons=reasons,
            attacker_state=attacker_state,
            defender_state=defender_state,
            gap=gap,
        )

    attacker_pace = _Pace(
        attacker_fit.intercept_s, attacker_fit.deg_rate_s_per_lap, attacker_state.tyre_age
    )
    defender_pace = _Pace(
        defender_fit.intercept_s, defender_fit.deg_rate_s_per_lap, defender_state.tyre_age
    )
    compound_delta = _compound_delta(params, attacker_state.compound, attacker_new_compound)

    horizon_lap = resolved_decision + params.undercut_horizon_laps
    if total_laps is not None:
        # The second stop's out-lap must still fit inside the race.
        horizon_lap = min(horizon_lap, total_laps - 1)

    margin = _margin_fn(kind, attacker_pace, defender_pace, gap, compound_delta, resolved_decision)
    earliest_second_stop = resolved_decision + FIRST_COVER_STAGGER_LAPS
    scan = range(earliest_second_stop, horizon_lap + 1)
    if not scan:
        reasons.append("race ends before a covered stop cycle completes")

    crossover_lap, margin_at_crossover = _scan_crossover(kind, margin, scan)
    margin_if_covered = margin(earliest_second_stop) if scan else None
    margin_band = (
        _covered_margin_band(
            kind,
            attacker_pace,
            defender_pace,
            attacker_fit,
            defender_fit,
            gap,
            compound_delta,
            resolved_decision,
            earliest_second_stop,
        )
        if scan
        else (None, None)
    )

    window, opens_in = _window_state(
        kind,
        margin_if_covered,
        attacker_pace,
        defender_pace,
        gap,
        compound_delta,
        resolved_decision,
        horizon_lap,
    )
    if window is WindowState.CLOSED:
        reasons.append(
            f"does not come through within the horizon (in-lap <= {horizon_lap})"
            if kind is CrossoverKind.UNDERCUT
            else "staying out does not keep the place even at the earliest response"
        )

    confidence = _reliability_confidence(attacker_fit, defender_fit)
    for label, fit in (("attacker", attacker_fit), ("defender", defender_fit)):
        if fit.reliability is not Reliability.GOOD:
            reasons.append(f"{label} fit is {fit.reliability.value} ({fit.n_laps_used} laps)")
    low, high = margin_band
    if low is not None and high is not None and min(low, high) < 0 < max(low, high):
        confidence = _cap(confidence, Confidence.MEDIUM)
        reasons.append("decision margin changes sign within the deg-rate 95% CIs")
    for row in (attacker_row, defender_row):
        status = _non_green_status(row)
        if status is not None:
            confidence = _cap(confidence, Confidence.LOW)
            reasons.append(
                f"track not green at the decision lap (status: {status}) — "
                "gap and pace are not race-representative"
            )
            break

    assumed_margin: float | None = None
    assumed_band: tuple[float | None, float | None] = (None, None)
    if assumed_second_stop_lap is not None and assumed_second_stop_lap >= earliest_second_stop:
        assumed_margin = margin(assumed_second_stop_lap)
        assumed_band = _covered_margin_band(
            kind,
            attacker_pace,
            defender_pace,
            attacker_fit,
            defender_fit,
            gap,
            compound_delta,
            resolved_decision,
            assumed_second_stop_lap,
        )

    if kind is CrossoverKind.UNDERCUT:
        rejoin = _rejoin_gap(
            attacker_pace,
            defender_pace,
            gap,
            pit_loss,
            params.undercut_out_lap_penalty_s,
            compound_delta,
        )
    else:
        rejoin = _rejoin_gap(
            defender_pace,
            attacker_pace,
            -gap,
            pit_loss,
            params.undercut_out_lap_penalty_s,
            0.0,
        )

    return CrossoverResult(
        kind=kind,
        attacker=attacker,
        defender=defender,
        decision_lap=resolved_decision,
        attacker_stop_lap=resolved_decision + 1 if kind is CrossoverKind.UNDERCUT else None,
        defender_stop_lap=resolved_decision + 1 if kind is CrossoverKind.OVERCUT else None,
        gap_s=gap,
        pit_loss_s=pit_loss,
        pit_loss_provenance=pit_loss_provenance,
        out_lap_penalty_s=params.undercut_out_lap_penalty_s,
        crossover_lap=crossover_lap,
        projected_gap_at_crossover_s=margin_at_crossover,
        projected_gap_if_covered_s=margin_if_covered,
        projected_gap_if_covered_low_s=low,
        projected_gap_if_covered_high_s=high,
        projected_margin_at_assumed_stop_s=assumed_margin,
        projected_margin_at_assumed_stop_low_s=assumed_band[0],
        projected_margin_at_assumed_stop_high_s=assumed_band[1],
        projected_rejoin_gap_s=rejoin,
        window=window,
        opens_in_laps=opens_in,
        horizon_lap=horizon_lap,
        confidence=confidence,
        reasons=tuple(reasons),
        attacker_state=attacker_state,
        defender_state=defender_state,
    )


def _scan_crossover(
    kind: CrossoverKind, margin: Callable[[int], float], scan: range
) -> tuple[int | None, float | None]:
    """Undercut: the FIRST second-stop lap with a positive margin (the
    defender staying out any longer only helps the attacker more). Overcut:
    the LAST one — the deadline before banked advantage runs out."""
    if kind is CrossoverKind.UNDERCUT:
        for lap in scan:
            value = margin(lap)
            if value > 0:
                return lap, value
        return None, None
    best: tuple[int | None, float | None] = (None, None)
    for lap in scan:
        value = margin(lap)
        if value > 0:
            best = (lap, value)
        elif best[0] is not None:
            break  # the deadline has passed; later laps only get worse
    return best


def _covered_margin_band(
    kind: CrossoverKind,
    attacker_pace: _Pace,
    defender_pace: _Pace,
    attacker_fit: DegradationFit,
    defender_fit: DegradationFit,
    gap: float,
    compound_delta: float,
    decision_lap: int,
    earliest_second_stop: int,
) -> tuple[float | None, float | None]:
    """The earliest-cover margin at the two worst-case combinations of the
    fitted rates' 95% CIs — attacker slow + defender fast, and the
    reverse. Not a real confidence interval on the margin (the CIs are
    per-fit, not joint), but exactly the sanity band a strategist wants:
    does the call survive the fits being wrong in the unhelpful
    direction?"""
    combos = (
        (attacker_fit.deg_rate_ci_high_s_per_lap, defender_fit.deg_rate_ci_low_s_per_lap),
        (attacker_fit.deg_rate_ci_low_s_per_lap, defender_fit.deg_rate_ci_high_s_per_lap),
    )
    values: list[float] = []
    for attacker_rate, defender_rate in combos:
        margin = _margin_fn(
            kind,
            attacker_pace.with_rate(attacker_rate),
            defender_pace.with_rate(defender_rate),
            gap,
            compound_delta,
            decision_lap,
        )
        values.append(margin(earliest_second_stop))
    return min(values), max(values)


def _window_state(
    kind: CrossoverKind,
    margin_if_covered: float | None,
    attacker_pace: _Pace,
    defender_pace: _Pace,
    gap: float,
    compound_delta: float,
    decision_lap: int,
    horizon_lap: int,
) -> tuple[WindowState, int | None]:
    """OPEN / OPENS_LATER / CLOSED, plus the countdown.

    The countdown scan advances BOTH cars along their fitted pace (the gap
    evolves by their staying-out difference each lap, tyres age in step)
    and asks, at each future decision lap, whether pitting then would beat
    an immediate cover. It assumes the defender does not stop in the
    meantime — every strategy countdown is conditional on that."""
    if margin_if_covered is None:
        return WindowState.CLOSED, None
    if margin_if_covered > 0:
        # A deadline (overcut) has nothing to open — the countdown is an
        # undercut concept.
        return WindowState.OPEN, 0 if kind is CrossoverKind.UNDERCUT else None
    if kind is CrossoverKind.OVERCUT:
        return WindowState.CLOSED, None

    gap_after_wait = gap
    max_wait = horizon_lap - decision_lap - FIRST_COVER_STAGGER_LAPS
    for wait in range(1, max_wait + 1):
        gap_after_wait += attacker_pace.staying_out(wait) - defender_pace.staying_out(wait)
        margin = _margin_fn(
            kind,
            attacker_pace.aged(wait),
            defender_pace.aged(wait),
            gap_after_wait,
            compound_delta,
            decision_lap + wait,
        )
        if margin(decision_lap + wait + FIRST_COVER_STAGGER_LAPS) > 0:
            return WindowState.OPENS_LATER, wait
    return WindowState.CLOSED, None


def _unknown_result(
    kind: CrossoverKind,
    attacker: str,
    defender: str,
    *,
    decision_lap: int,
    pit_loss: float,
    pit_loss_provenance: str,
    params: KernelParams,
    reasons: list[str],
    attacker_state: DriverStintState,
    defender_state: DriverStintState,
    gap: float | None,
) -> CrossoverResult:
    """The honest non-answer: window UNKNOWN, confidence LOW, and reasons
    saying exactly which input was missing."""
    return CrossoverResult(
        kind=kind,
        attacker=attacker,
        defender=defender,
        decision_lap=decision_lap,
        attacker_stop_lap=decision_lap + 1 if kind is CrossoverKind.UNDERCUT else None,
        defender_stop_lap=decision_lap + 1 if kind is CrossoverKind.OVERCUT else None,
        gap_s=gap,
        pit_loss_s=pit_loss,
        pit_loss_provenance=pit_loss_provenance,
        out_lap_penalty_s=params.undercut_out_lap_penalty_s,
        crossover_lap=None,
        projected_gap_at_crossover_s=None,
        projected_gap_if_covered_s=None,
        projected_gap_if_covered_low_s=None,
        projected_gap_if_covered_high_s=None,
        projected_margin_at_assumed_stop_s=None,
        projected_margin_at_assumed_stop_low_s=None,
        projected_margin_at_assumed_stop_high_s=None,
        projected_rejoin_gap_s=None,
        window=WindowState.UNKNOWN,
        opens_in_laps=None,
        horizon_lap=decision_lap,
        confidence=Confidence.LOW,
        reasons=tuple(reasons),
        attacker_state=attacker_state,
        defender_state=defender_state,
    )


# ---------------------------------------------------------------------------
# the per-lap live primitive
# ---------------------------------------------------------------------------


def undercut_snapshot(
    state: SessionFrames,
    params: KernelParams,
    *,
    decision_lap: int | None = None,
    total_laps: int | None = None,
) -> list[CrossoverResult]:
    """One `undercut_window` per adjacent pair of cars — the per-lap
    primitive Phase 3 renders as a countdown column ("Undercut on Norris
    opens in 3 laps").

    Call it after every completed lap with the frames built so far. It
    slices the laps to `decision_lap` (defaulting to the latest lap at
    least two cars have completed), refits degradation on exactly that
    slice — the walk-forward contract, enforced rather than trusted — and
    pairs cars adjacent in race order (by lap-completion timestamp at the
    decision lap) whose gap is within `params.undercut_max_gap_s`.

    `total_laps` should be the scheduled race distance when known (live);
    it defaults to the highest lap in `state.laps`, which is only right
    once the race is over — fine for historical frames, an underestimate
    mid-race (it shortens the horizon and stiffens the fuel model, both
    conservative).

    Pure and deterministic. Results come back in race order.
    """
    laps = state.laps
    if laps.is_empty():
        return []

    resolved_total = total_laps if total_laps is not None else _as_int(laps["lap_number"].max())
    resolved_decision = decision_lap
    if resolved_decision is None:
        populated = (
            laps.group_by("lap_number")
            .agg(pl.col("driver").n_unique().alias("n"))
            .filter(pl.col("n") >= 2)
        )
        if populated.is_empty():
            return []
        resolved_decision = _as_int(populated["lap_number"].max())
    if resolved_decision is None:
        return []

    sliced = laps.filter(pl.col("lap_number") <= resolved_decision)
    sliced_state = SessionFrames(
        session_key=state.session_key,
        laps=sliced,
        stints=state.stints.filter(pl.col("start_lap") <= resolved_decision)
        if not state.stints.is_empty()
        else state.stints,
        circuit=state.circuit,
        event_name=state.event_name,
    )
    fits = fit_degradation(
        fuel_correct(clean_laps(sliced, params), params, total_laps=resolved_total), params
    )

    order = (
        sliced.filter(
            (pl.col("lap_number") == resolved_decision) & pl.col("lap_end_utc").is_not_null()
        )
        .sort("lap_end_utc")["driver"]
        .to_list()
    )
    results: list[CrossoverResult] = []
    for ahead, behind in pairwise(order):
        result = undercut_window(
            sliced_state,
            str(behind),
            str(ahead),
            fits,
            params,
            decision_lap=resolved_decision,
            total_laps=resolved_total,
        )
        if result.gap_s is not None and result.gap_s > params.undercut_max_gap_s:
            continue
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

CROSSOVER_RESULT_SCHEMA: Schema = {
    "kind": pl.String(),
    "attacker": pl.String(),
    "defender": pl.String(),
    "decision_lap": pl.Int32(),
    "attacker_stop_lap": pl.Int32(),
    "defender_stop_lap": pl.Int32(),
    "gap_s": pl.Float64(),
    "pit_loss_s": pl.Float64(),
    "pit_loss_provenance": pl.String(),
    "crossover_lap": pl.Int32(),
    "projected_gap_at_crossover_s": pl.Float64(),
    "projected_gap_if_covered_s": pl.Float64(),
    "projected_gap_if_covered_low_s": pl.Float64(),
    "projected_gap_if_covered_high_s": pl.Float64(),
    "projected_margin_at_assumed_stop_s": pl.Float64(),
    "projected_margin_at_assumed_stop_low_s": pl.Float64(),
    "projected_margin_at_assumed_stop_high_s": pl.Float64(),
    "projected_rejoin_gap_s": pl.Float64(),
    "window": pl.String(),
    "opens_in_laps": pl.Int32(),
    "horizon_lap": pl.Int32(),
    "confidence": pl.String(),
    "attacker_compound": pl.String(),
    "attacker_tyre_age": pl.Int32(),
    "attacker_deg_rate_s_per_lap": pl.Float64(),
    "attacker_fit_reliability": pl.String(),
    "defender_compound": pl.String(),
    "defender_tyre_age": pl.Int32(),
    "defender_deg_rate_s_per_lap": pl.Float64(),
    "defender_fit_reliability": pl.String(),
    "reasons": pl.String(),
}


def crossover_results_to_frame(results: Sequence[CrossoverResult]) -> pl.DataFrame:
    """`CrossoverResult`s flattened to one row each — what a backtest
    tabulates and a UI serializes. `reasons` joins to '; ' so the frame
    stays flat."""
    if not results:
        return pl.DataFrame(schema=CROSSOVER_RESULT_SCHEMA)
    rows = [
        {
            "kind": r.kind.value,
            "attacker": r.attacker,
            "defender": r.defender,
            "decision_lap": r.decision_lap,
            "attacker_stop_lap": r.attacker_stop_lap,
            "defender_stop_lap": r.defender_stop_lap,
            "gap_s": r.gap_s,
            "pit_loss_s": r.pit_loss_s,
            "pit_loss_provenance": r.pit_loss_provenance,
            "crossover_lap": r.crossover_lap,
            "projected_gap_at_crossover_s": r.projected_gap_at_crossover_s,
            "projected_gap_if_covered_s": r.projected_gap_if_covered_s,
            "projected_gap_if_covered_low_s": r.projected_gap_if_covered_low_s,
            "projected_gap_if_covered_high_s": r.projected_gap_if_covered_high_s,
            "projected_margin_at_assumed_stop_s": r.projected_margin_at_assumed_stop_s,
            "projected_margin_at_assumed_stop_low_s": r.projected_margin_at_assumed_stop_low_s,
            "projected_margin_at_assumed_stop_high_s": r.projected_margin_at_assumed_stop_high_s,
            "projected_rejoin_gap_s": r.projected_rejoin_gap_s,
            "window": r.window.value,
            "opens_in_laps": r.opens_in_laps,
            "horizon_lap": r.horizon_lap,
            "confidence": r.confidence.value,
            "attacker_compound": r.attacker_state.compound,
            "attacker_tyre_age": r.attacker_state.tyre_age,
            "attacker_deg_rate_s_per_lap": r.attacker_state.deg_rate_s_per_lap,
            "attacker_fit_reliability": r.attacker_state.fit_reliability,
            "defender_compound": r.defender_state.compound,
            "defender_tyre_age": r.defender_state.tyre_age,
            "defender_deg_rate_s_per_lap": r.defender_state.deg_rate_s_per_lap,
            "defender_fit_reliability": r.defender_state.fit_reliability,
            "reasons": "; ".join(r.reasons),
        }
        for r in results
    ]
    return pl.DataFrame(rows, schema=CROSSOVER_RESULT_SCHEMA)


__all__ = [
    "CROSSOVER_RESULT_SCHEMA",
    "FIRST_COVER_STAGGER_LAPS",
    "Confidence",
    "CrossoverKind",
    "CrossoverResult",
    "DriverStintState",
    "WindowState",
    "crossover_results_to_frame",
    "overcut_window",
    "undercut_snapshot",
    "undercut_window",
]
