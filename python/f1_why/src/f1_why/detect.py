"""`detect_anomalies`: Phase 5.1 — deciding WHEN something is worth
explaining. Pure Python over the kernel's own typed frames and its
`KernelReport`; no LLM, no evidence bundle, no I/O, no clock. That is the
line CLAUDE.md draws for this prompt: detection only. A later Phase 5
prompt fuses telemetry/radio/race-control into an evidence bundle for
whichever anomalies this module surfaces, and only then does an LLM ever
get called.

Six kinds, one per `f1_why.anomaly.AnomalyKind` member (see that module for
what each one means and its `magnitude`'s unit):

- **pace_loss** / **off_track** share one robust-outlier model: a lap's
  whole-lap (pace_loss) or one mini-sector's (off_track, the lock-up/
  off-track proxy — this schema carries sector splits, not a telemetry
  brake channel, so that is the signal available here) time versus a
  median/MAD baseline built ONLY from that same (driver, stint)'s own
  CLEAN laps — deliberately a NEW, independent bound
  (`AnomalyParams.outlier_mad_multiplier`/`outlier_floor_s`), not a reuse of
  `KernelParams.outlier_mad_multiplier` (that one decides fitting
  eligibility; this one decides what is worth explaining, a different
  question that should not silently move if the other is retuned).
- **position_change** walks each driver's own `position` column
  lap-over-lap, skipping any lap touched by that driver's own pit cycle,
  and pairs a clean one-for-one swap into one Anomaly naming both drivers.
- **pit_anomaly** reuses `f1_kernel.pit_loss.pit_loss_samples` (the same
  green-flag-only, fuel-corrected, neighbour-baselined measurement the
  calibration tooling itself uses) and flags a stop far from the circuit's
  own calibrated (or default) norm.
- **deg_cliff** reads `KernelReport.degradation_fits`' own optional
  quadratic term directly — no new fitting here, just a threshold on what
  the kernel already computed.
- **undercut_resolved** reuses `f1_kernel.undercut_eval.evaluate_session_stops`
  (the walk-forward backtest machinery) to find every REAL stop-pair duel
  this session's own prediction spoke to, and reports how it resolved.

Every anomaly carries a `salience` (see `_salience`) so a consumer can
rank/limit rather than being handed every 0.05s wobble — CLAUDE.md's own
framing for this prompt. Detection is generous by design: kinds are not
aggressively pre-filtered beyond "is there a genuine, robustly-supported
signal at all", and salience is what separates a race-defining moment from
a minor one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

import numpy as np
import polars as pl

from f1_kernel import (
    Confidence,
    KernelReport,
    Reliability,
    SessionFrames,
    evaluate_session_stops,
    fuel_correct,
    pit_loss_samples,
)
from f1_why.anomaly import Anomaly, AnomalyKind
from f1_why.params import AnomalyParams

MAD_TO_SIGMA = 1.4826
"""See `f1_kernel.cleaning.MAD_TO_SIGMA` — the same normal-consistency
scale, redefined locally rather than imported: `f1_kernel.pit_loss` sets
the same precedent for this exact constant, treating it as a generically-
known statistical fact rather than a cross-module dependency."""

_CONTEXT_EXCLUDED_REASONS: tuple[str, ...] = (
    "is_lap1",
    "is_in_lap",
    "is_out_lap",
    "is_sc",
    "is_vsc",
    "is_yellow",
    "is_red",
)
"""Laps already fully explained by known context — never candidates for
PACE_LOSS/OFF_TRACK. Unlike `f1_kernel.cleaning.REASON_COLUMNS`, dirty-air
and already-`is_anomalous` laps are deliberately NOT excluded here: they
are exactly the unexplained residue this detector exists to surface."""

_SECTOR_COLUMNS: tuple[str, ...] = ("sector1_s", "sector2_s", "sector3_s")


def detect_anomalies(
    session_frames: SessionFrames,
    kernel_report: KernelReport,
    params: AnomalyParams,
) -> list[Anomaly]:
    """Detect everything in `session_frames`/`kernel_report` worth asking a
    future "why" step to explain.

    Pure and deterministic: no I/O, no clock, no network, same frames and
    report in, an identical (and identically ORDERED) list out — the same
    guarantee every `f1_kernel` analytic gives. `session_frames` and
    `kernel_report` must describe the SAME session — ordinarily
    `kernel_report = analyze_session_from_frames(session_frames, ...)` —
    this does not re-verify that, the same trust
    `f1_kernel.frames.SessionFrames` itself asks callers to honour.

    Returns anomalies sorted by `salience` descending (most explain-worthy
    first), with a stable secondary order (lap, kind, drivers) so ties never
    depend on internal iteration order. Detection only, per CLAUDE.md's
    Phase 5 invariants: no evidence bundle is assembled and no LLM is
    called here.
    """
    if session_frames.laps.is_empty():
        return []

    annotated = fuel_correct(
        kernel_report.annotated_laps, kernel_report.params, total_laps=kernel_report.total_laps
    )

    anomalies: list[Anomaly] = [
        *_detect_pace_loss(annotated, params),
        *_detect_off_track(annotated, params),
        *_detect_position_change(session_frames, params),
        *_detect_pit_anomaly(session_frames, kernel_report, params),
        *_detect_deg_cliff(session_frames, kernel_report, params),
        *_detect_undercut_resolved(session_frames, kernel_report, params),
    ]
    return sorted(
        anomalies,
        key=lambda a: (
            -a.salience,
            a.lap_number if a.lap_number is not None else -1,
            a.kind.value,
            a.drivers,
        ),
    )


# ---------------------------------------------------------------------------
# shared: the robust-outlier baseline (pace_loss, off_track)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RobustBaseline:
    median: float
    bound: float
    n_reference: int


def _baseline(reference: Sequence[float], params: AnomalyParams) -> _RobustBaseline | None:
    """Median + a one-directional (slower-only) MAD bound over `reference`,
    or `None` when there are too few reference laps to trust one at all."""
    if len(reference) < params.min_reference_laps:
        return None
    values = np.asarray(reference, dtype=float)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    bound = max(MAD_TO_SIGMA * params.outlier_mad_multiplier * mad, params.outlier_floor_s)
    return _RobustBaseline(median=median, bound=bound, n_reference=len(reference))


def _is_context_excluded(row: dict[str, object]) -> bool:
    return any(bool(row.get(reason)) for reason in _CONTEXT_EXCLUDED_REASONS)


def _trailing_windows(
    is_clean_values: Sequence[tuple[bool, float | None]], lookback: int
) -> list[list[float]]:
    """For index `i`, the up to `lookback` most recent CLEAN, non-null
    values at indices strictly before `i`, in original (lap) order — the
    causal "recent trend" window `pace_loss`/`off_track` compare a
    candidate against. TRAILING rather than whole-stint on purpose: a stint
    with genuine (non-anomalous) degradation curvature drifts slower over
    its own length, and a late lap compared against the whole stint's
    average would misread ordinary late-stint pace as a false positive."""
    windows: list[list[float]] = []
    clean_so_far: list[float] = []
    for is_clean, value in is_clean_values:
        windows.append(clean_so_far[-lookback:] if lookback > 0 else [])
        if is_clean and value is not None:
            clean_so_far.append(value)
    return windows


def _detect_pace_loss(annotated: pl.DataFrame, params: AnomalyParams) -> list[Anomaly]:
    """PACE_LOSS: a lap whose fuel-corrected time is a robust outlier,
    slower, versus its own stint's recent (trailing) clean-lap trend."""
    anomalies: list[Anomaly] = []
    for (driver_value, stint_value), group in annotated.group_by(
        ["driver", "stint_number"], maintain_order=True
    ):
        driver = str(driver_value)
        rows = group.sort("lap_number").to_dicts()
        clean_values = [
            (bool(row["is_clean"]), _as_float(row["lap_time_fuel_corrected_s"])) for row in rows
        ]
        windows = _trailing_windows(clean_values, params.lookback_laps)
        for row, window in zip(rows, windows, strict=True):
            value = row["lap_time_fuel_corrected_s"]
            if value is None or _is_context_excluded(row):
                continue
            baseline = _baseline(window, params)
            if baseline is None:
                continue
            delta = float(value) - baseline.median
            if delta <= baseline.bound:
                continue
            lap_number = int(row["lap_number"])
            anomalies.append(
                Anomaly(
                    kind=AnomalyKind.PACE_LOSS,
                    lap_number=lap_number,
                    anomaly_time=row["lap_end_utc"],
                    drivers=(driver,),
                    magnitude=delta,
                    salience=_salience(delta, params),
                    summary={
                        "observed_s": float(value),
                        "trend_s": baseline.median,
                        "delta_s": delta,
                        "stint_number": _as_int(stint_value),
                        "n_reference_laps": baseline.n_reference,
                    },
                    reasons=(
                        f"lap {lap_number} was {delta:.2f}s slower (fuel-corrected) than "
                        f"driver {driver}'s own {baseline.n_reference}-lap recent trend "
                        "this stint",
                    ),
                )
            )
    return anomalies


def _detect_off_track(annotated: pl.DataFrame, params: AnomalyParams) -> list[Anomaly]:
    """OFF_TRACK: one mini-sector's time is a robust outlier versus the
    driver's own recent (trailing) clean pace in that sector, in the same
    stint — a lock-up/off-track proxy localized to part of the lap, using
    the only sub-lap timing this schema carries. Sector times are not
    themselves fuel-corrected (the kernel has no per-sector fuel model);
    the residual within-stint fuel trend this leaves is a minor, accepted
    approximation, the same order as `pace_loss`'s own use of a trailing
    median rather than a fully re-derived per-sector correction."""
    anomalies: list[Anomaly] = []
    for (driver_value, stint_value), group in annotated.group_by(
        ["driver", "stint_number"], maintain_order=True
    ):
        driver = str(driver_value)
        rows = group.sort("lap_number").to_dicts()
        for sector_index, column in enumerate(_SECTOR_COLUMNS, start=1):
            clean_values = [(bool(row["is_clean"]), _as_float(row[column])) for row in rows]
            windows = _trailing_windows(clean_values, params.lookback_laps)
            for row, window in zip(rows, windows, strict=True):
                value = row[column]
                if value is None or _is_context_excluded(row):
                    continue
                baseline = _baseline(window, params)
                if baseline is None:
                    continue
                delta = float(value) - baseline.median
                if delta <= baseline.bound:
                    continue
                lap_number = int(row["lap_number"])
                anomalies.append(
                    Anomaly(
                        kind=AnomalyKind.OFF_TRACK,
                        lap_number=lap_number,
                        anomaly_time=row["lap_end_utc"],
                        drivers=(driver,),
                        magnitude=delta,
                        salience=_salience(delta, params),
                        summary={
                            "sector": sector_index,
                            "observed_s": float(value),
                            "trend_s": baseline.median,
                            "delta_s": delta,
                            "stint_number": _as_int(stint_value),
                            "n_reference_laps": baseline.n_reference,
                        },
                        reasons=(
                            f"sector {sector_index} on lap {lap_number} was {delta:.2f}s "
                            f"slower than driver {driver}'s own {baseline.n_reference}-lap "
                            "recent trend this stint — a sector-time proxy, not "
                            "telemetry-derived braking-point drift",
                        ),
                    )
                )
    return anomalies


# ---------------------------------------------------------------------------
# position change
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PositionMove:
    driver: str
    lap_number: int
    from_position: int
    to_position: int
    lap_end_utc: datetime

    @property
    def places(self) -> int:
        """Positive means the driver GAINED places this lap."""
        return self.from_position - self.to_position


def _position_moves(session_frames: SessionFrames) -> list[_PositionMove]:
    """Every on-track, non-pit-cycle position change: one entry per
    (driver, lap) where classification moved between consecutive completed
    laps, with either lap touched by that driver's own pit stop excluded."""
    laps = session_frames.laps.select(
        "driver", "lap_number", "position", "is_pit_in", "is_pit_out", "lap_end_utc"
    )
    moves: list[_PositionMove] = []
    for driver in session_frames.drivers:
        rows = [
            row
            for row in laps.filter(pl.col("driver") == driver).sort("lap_number").to_dicts()
            if row["position"] is not None and row["lap_end_utc"] is not None
        ]
        for previous, current in pairwise(rows):
            if int(current["lap_number"]) != int(previous["lap_number"]) + 1:
                continue  # a gap in the record — do not compare across it
            if any(
                bool(row["is_pit_in"]) or bool(row["is_pit_out"]) for row in (previous, current)
            ):
                continue  # a pit cycle, not an on-track move
            if current["position"] == previous["position"]:
                continue
            moves.append(
                _PositionMove(
                    driver=driver,
                    lap_number=int(current["lap_number"]),
                    from_position=int(previous["position"]),
                    to_position=int(current["position"]),
                    lap_end_utc=current["lap_end_utc"],
                )
            )
    return moves


def _detect_position_change(session_frames: SessionFrames, params: AnomalyParams) -> list[Anomaly]:
    """POSITION_CHANGE: an on-track overtake, never a pit-cycle reshuffle.
    A clean one-for-one swap becomes a single Anomaly naming both drivers;
    a move with no isolable rival (a multi-car shuffle) is reported for the
    one driver alone."""
    by_lap: dict[int, list[_PositionMove]] = defaultdict(list)
    for move in _position_moves(session_frames):
        by_lap[move.lap_number].append(move)

    anomalies: list[Anomaly] = []
    for lap_number in sorted(by_lap):
        lap_moves = by_lap[lap_number]
        gainers = [move for move in lap_moves if move.places > 0]
        losers = [move for move in lap_moves if move.places < 0]
        paired_losers: set[str] = set()
        for gainer in gainers:
            partner = next(
                (
                    loser
                    for loser in losers
                    if loser.driver not in paired_losers
                    and loser.from_position == gainer.to_position
                    and loser.to_position == gainer.from_position
                ),
                None,
            )
            if partner is not None:
                paired_losers.add(partner.driver)
            anomalies.append(_position_change_anomaly(gainer, partner, params))
        for loser in losers:
            if loser.driver not in paired_losers:
                anomalies.append(_position_change_anomaly(loser, None, params))
    return anomalies


def _position_change_anomaly(
    primary: _PositionMove, partner: _PositionMove | None, params: AnomalyParams
) -> Anomaly:
    """`primary` is always the driver whose move is being reported from;
    `partner`, when given, is always the other half of a clean swap found
    from the GAINER's side (see `_detect_position_change`)."""
    places = abs(primary.places)
    gained = primary.places > 0
    drivers = (primary.driver, partner.driver) if partner is not None else (primary.driver,)
    best_position = min(primary.to_position, primary.from_position)
    seconds_equivalent = (
        places
        * params.position_change_seconds_per_place
        * (1.0 + params.position_change_lead_bonus / max(best_position, 1))
    )
    reason = (
        f"{'gained' if gained else 'lost'} {places} place(s) on track at lap "
        f"{primary.lap_number} (P{primary.from_position} -> P{primary.to_position})"
    )
    reason += (
        f", swapping with {partner.driver}"
        if partner is not None
        else " — no single rival isolable, likely a multi-car shuffle"
    )
    return Anomaly(
        kind=AnomalyKind.POSITION_CHANGE,
        lap_number=primary.lap_number,
        anomaly_time=primary.lap_end_utc,
        drivers=drivers,
        magnitude=float(places),
        salience=_salience(seconds_equivalent, params),
        summary={
            "from_position": primary.from_position,
            "to_position": primary.to_position,
            "places": places,
            "gained": gained,
            "rival": partner.driver if partner is not None else None,
        },
        reasons=(reason,),
    )


# ---------------------------------------------------------------------------
# pit anomaly
# ---------------------------------------------------------------------------


def _detect_pit_anomaly(
    session_frames: SessionFrames, kernel_report: KernelReport, params: AnomalyParams
) -> list[Anomaly]:
    """PIT_ANOMALY: a green-flag stop's measured cost is well outside the
    calibrated norm for this circuit (or the cross-circuit default when no
    circuit metadata ships for this event)."""
    samples, _excluded = pit_loss_samples(
        session_frames.laps,
        kernel_report.params,
        event_name=session_frames.event_name,
        total_laps=kernel_report.total_laps,
    )
    if not samples:
        return []

    circuit = session_frames.circuit
    expected = kernel_report.params.pit_loss_for(
        circuit_id=circuit.circuit_id if circuit is not None else None,
        circuit_pit_loss_s=circuit.pit_loss_s if circuit is not None else None,
    )
    provenance = circuit.pit_loss_provenance if circuit is not None else "default"

    lap_end_utc_by_key = _lap_end_utc_index(session_frames.laps)

    anomalies: list[Anomaly] = []
    for sample in samples:
        delta = sample.pit_loss_s - expected
        if abs(delta) < params.pit_anomaly_min_excess_s:
            continue
        anomaly_time = lap_end_utc_by_key.get((sample.driver, sample.out_lap))
        if anomaly_time is None:
            continue  # no timestamp to anchor this anomaly to
        direction = "slower" if delta > 0 else "faster"
        anomalies.append(
            Anomaly(
                kind=AnomalyKind.PIT_ANOMALY,
                lap_number=sample.in_lap,
                anomaly_time=anomaly_time,
                drivers=(sample.driver,),
                magnitude=abs(delta),
                salience=_salience(abs(delta), params),
                summary={
                    "in_lap": sample.in_lap,
                    "out_lap": sample.out_lap,
                    "observed_pit_loss_s": sample.pit_loss_s,
                    "expected_pit_loss_s": expected,
                    "expected_provenance": provenance,
                    "delta_s": delta,
                },
                reasons=(
                    f"stop on lap {sample.in_lap} cost {sample.pit_loss_s:.1f}s, "
                    f"{abs(delta):.1f}s {direction} than the {provenance} norm "
                    f"({expected:.1f}s) for this circuit",
                ),
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# degradation cliff
# ---------------------------------------------------------------------------


def _detect_deg_cliff(
    session_frames: SessionFrames, kernel_report: KernelReport, params: AnomalyParams
) -> list[Anomaly]:
    """DEG_CLIFF: a stint's degradation fit shows a non-linear (quadratic)
    falloff large enough, by the last lap the fit used, to matter. No new
    fitting happens here — this is a threshold on what `fit_degradation`
    already computed."""
    if session_frames.stints.is_empty() or not kernel_report.degradation_fits:
        return []

    stints_by_key = {
        (str(row["driver"]), int(row["stint_number"])): row
        for row in session_frames.stints.select(
            "driver", "stint_number", "start_lap", "end_lap", "tyre_age_at_start"
        ).to_dicts()
        if row["stint_number"] is not None
    }
    lap_end_utc_by_key = _lap_end_utc_index(session_frames.laps)

    anomalies: list[Anomaly] = []
    for fit in kernel_report.degradation_fits:
        term = fit.quadratic_term_s_per_lap2
        if term is None or term < params.deg_cliff_min_quadratic_term_s_per_lap2:
            continue
        stint_row = stints_by_key.get((fit.driver, fit.stint_number))
        if stint_row is None or stint_row["end_lap"] is None or stint_row["start_lap"] is None:
            continue
        end_lap = int(stint_row["end_lap"])
        age_at_end = (
            end_lap - int(stint_row["start_lap"]) + 1 + int(stint_row["tyre_age_at_start"] or 0)
        )
        extra_time_s = term * (age_at_end**2)
        if extra_time_s < params.deg_cliff_min_extra_time_s:
            continue
        anomaly_time = lap_end_utc_by_key.get((fit.driver, end_lap))
        if anomaly_time is None:
            continue

        salience = _salience(extra_time_s, params)
        reasons = [
            f"stint {fit.stint_number} ({fit.compound}) shows a {extra_time_s:.1f}s cliff "
            f"by lap {end_lap} on top of its linear degradation rate"
        ]
        if fit.reliability is Reliability.UNRELIABLE:
            salience *= params.unreliable_fit_derate
            reasons.append("underlying degradation fit is unreliable — treat this cautiously")

        anomalies.append(
            Anomaly(
                kind=AnomalyKind.DEG_CLIFF,
                lap_number=end_lap,
                anomaly_time=anomaly_time,
                drivers=(fit.driver,),
                magnitude=extra_time_s,
                salience=salience,
                summary={
                    "stint_number": fit.stint_number,
                    "compound": fit.compound,
                    "quadratic_term_s_per_lap2": term,
                    "tyre_age_at_end": age_at_end,
                    "extra_time_s": extra_time_s,
                    "fit_reliability": fit.reliability.value,
                },
                reasons=tuple(reasons),
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# undercut resolved
# ---------------------------------------------------------------------------


def _detect_undercut_resolved(
    session_frames: SessionFrames, kernel_report: KernelReport, params: AnomalyParams
) -> list[Anomaly]:
    """UNDERCUT_RESOLVED: a real stop-pair duel this session's own
    walk-forward prediction spoke to has resolved (both cars completed
    their post-stop lap) — scored against what actually happened exactly
    as `f1_kernel.undercut_eval`'s own backtest does, reused here per
    session rather than re-derived."""
    lap_end_utc_by_key = _lap_end_utc_index(session_frames.laps)

    anomalies: list[Anomaly] = []
    for outcome in evaluate_session_stops(session_frames, kernel_report.params):
        if (
            not outcome.evaluated
            or outcome.predicted_ahead is None
            or outcome.hit is None
            or outcome.actual_margin_s is None
            or outcome.truth_lap is None
            or outcome.confidence is None
        ):
            continue

        confidence = Confidence(outcome.confidence)
        weight = params.undercut_resolved_confidence_weight.get(confidence.value, 1.0)
        upset = confidence is Confidence.HIGH and not outcome.hit
        if upset:
            weight *= params.undercut_resolved_upset_multiplier
        seconds_equivalent = abs(outcome.actual_margin_s) * weight

        anomaly_time = lap_end_utc_by_key.get(
            (outcome.attacker, outcome.truth_lap)
        ) or lap_end_utc_by_key.get((outcome.defender, outcome.truth_lap))
        if anomaly_time is None:
            continue

        reasons = [
            f"{outcome.kind} call ({outcome.confidence} confidence) made at lap "
            f"{outcome.decision_lap} {'held' if outcome.hit else 'failed'}: "
            f"{outcome.attacker} finished {abs(outcome.actual_margin_s):.1f}s "
            f"{'ahead of' if outcome.actual_ahead else 'behind'} {outcome.defender}"
        ]
        if upset:
            reasons.append("a high-confidence call that did not come through — an upset")

        anomalies.append(
            Anomaly(
                kind=AnomalyKind.UNDERCUT_RESOLVED,
                lap_number=outcome.truth_lap,
                anomaly_time=anomaly_time,
                drivers=(outcome.attacker, outcome.defender),
                magnitude=abs(outcome.actual_margin_s),
                salience=_salience(seconds_equivalent, params),
                summary={
                    "undercut_kind": outcome.kind,
                    "decision_lap": outcome.decision_lap,
                    "predicted_ahead": outcome.predicted_ahead,
                    "actual_ahead": outcome.actual_ahead,
                    "hit": outcome.hit,
                    "confidence": outcome.confidence,
                    "actual_margin_s": outcome.actual_margin_s,
                    "predicted_margin_s": outcome.predicted_margin_s,
                },
                reasons=tuple(reasons),
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# salience
# ---------------------------------------------------------------------------


def _salience(seconds_equivalent: float, params: AnomalyParams) -> float:
    """Bounded, monotonic mapping from a kind's own "how many seconds of
    race-significance" figure onto one [0, 1) ranking scale shared by every
    kind: `1 - exp(-x / scale)`, saturating for a big event, near zero for
    a tiny one — so the frontend can rank/limit without knowing each kind's
    own unit."""
    clamped = max(0.0, seconds_equivalent)
    return 1.0 - math.exp(-clamped / params.salience_scale_s)


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _lap_end_utc_index(laps: pl.DataFrame) -> dict[tuple[str, int], datetime]:
    """`(driver, lap_number) -> lap_end_utc`, the anchor timestamp every
    anomaly kind resolves its `anomaly_time` from."""
    return {
        (str(row["driver"]), int(row["lap_number"])): row["lap_end_utc"]
        for row in laps.select("driver", "lap_number", "lap_end_utc").to_dicts()
        if row["lap_end_utc"] is not None
    }


__all__ = ["detect_anomalies"]
