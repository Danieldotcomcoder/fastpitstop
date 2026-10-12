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
- **position_change** compares each lap's running order with the previous
  lap's. A gain only counts for the cars passed ON TRACK: a car that was on
  an in- or out-lap, or had stopped, was never overtaken, and a car moving
  up because the cars ahead pitted or retired is not an overtake. That rule
  is what the old per-driver walk missed: in the 2024 Bahrain GP most of
  its "overtakes" were the first pit window (laps 11-15) reshuffling the
  field. Neutralized laps and lap 1 (see race_start) never count.
- **pit_anomaly** reuses `f1_kernel.pit_loss.pit_loss_samples` (the same
  green-flag-only, fuel-corrected, neighbour-baselined measurement the
  calibration tooling itself uses) and flags a stop far from the circuit's
  own calibrated (or default) norm.
- **deg_cliff** reads `KernelReport.degradation_fits`' own optional
  quadratic term directly — no new fitting here, just a threshold on what
  the kernel already computed.
- **undercut_resolved** reuses `f1_kernel.undercut_eval.evaluate_session_stops`
  (the walk-forward backtest machinery) to find every REAL stop-pair duel
  this session's own prediction spoke to, and reports how it resolved. Its
  salience rests on whether the order CHANGED, not on the realized margin.
- **race_start** (needs the starting grid), **neutralization**,
  **retirement** and **race_finish** are the race's own landmarks, read
  from the lap frame: what lap 1 did to the grid, each safety car / VSC /
  red flag once it is over, each car that stopped racing, and the flag.

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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from f1_domain import DomainEvent, PositionChanged
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

_SLICKS: frozenset[str] = frozenset({"soft", "medium", "hard"})
"""Compounds whose pace falls away with their own wear. An intermediate on a
drying track does the opposite: the 2024 Canadian GP's Ocon "faded 5.5s" on
inters while lapping 3.5s quicker than five laps earlier."""


def detect_anomalies(
    session_frames: SessionFrames,
    kernel_report: KernelReport,
    params: AnomalyParams,
    *,
    grid: Mapping[str, int] | None = None,
) -> list[Anomaly]:
    """Detect everything in `session_frames`/`kernel_report` worth asking a
    future "why" step to explain.

    `grid` is the starting order (driver -> grid slot). The frames only
    carry each lap's END position, so without it lap 1 cannot be compared
    with anything: no `RACE_START`, and a car that never completed lap 1 is
    not reported as retired.

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
    table = _LapTable(kernel_report.annotated_laps)

    retirements = _detect_retirements(
        table, grid, kernel_report.total_laps, _stint_starts(session_frames.stints), params
    )
    anomalies: list[Anomaly] = [
        *_detect_pace_loss(annotated, params),
        *_detect_off_track(annotated, params),
        *_detect_position_change(table, params),
        *_detect_pit_anomaly(session_frames, kernel_report, table, params),
        *_detect_deg_cliff(session_frames, kernel_report, table, params),
        *_detect_undercut_resolved(session_frames, kernel_report, table, params),
        *_detect_race_start(table, grid, params),
        *_detect_neutralizations(table, grid, retirements, params),
        *retirements,
        *_detect_race_finish(table, kernel_report.total_laps, grid, params),
    ]
    anomalies = [_with_reveal(anomaly, table) for anomaly in anomalies]
    return sorted(
        anomalies,
        key=lambda a: (
            -a.salience,
            a.lap_number if a.lap_number is not None else -1,
            a.kind.value,
            a.drivers,
        ),
    )


def starting_grid(events: Iterable[DomainEvent], laps: pl.DataFrame) -> dict[str, int] | None:
    """The order the cars started in: each driver's last reported position
    at or before lap 1 began, re-ranked 1..N. `None` before lap 1 has begun
    or when the stream reported no order before it.

    The live feed reports the running order before the start (the grid);
    a backfilled race gets the same from its results' grid positions, which
    `f1_historical_adapter` emits at the session's start. One function, so
    both paths derive the grid identically."""
    if laps.is_empty() or "lap_start_utc" not in laps.columns:
        return None
    starts = laps.filter(pl.col("lap_number") == 1)["lap_start_utc"].drop_nulls()
    if starts.is_empty():
        return None
    lights_out = starts.min()
    if not isinstance(lights_out, datetime):
        return None
    latest: dict[str, tuple[datetime, int]] = {}
    for event in events:
        if not isinstance(event, PositionChanged) or event.driver is None or event.position is None:
            continue
        if event.ts_utc > lights_out:
            continue
        seen = latest.get(event.driver)
        if seen is None or event.ts_utc >= seen[0]:
            latest[event.driver] = (event.ts_utc, event.position)
    if len(latest) < 2:
        return None
    ordered = sorted(latest, key=lambda driver: (latest[driver][1], driver))
    return {driver: slot for slot, driver in enumerate(ordered, start=1)}


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


def _was_neutralized(row: dict[str, object]) -> bool:
    """A lap run under a safety car, VSC or red flag. The lap AFTER one is a
    restart and is slow for that reason; flagging it as a pace loss reported
    "Ocon lost 17.4s" on the 2024 São Paulo GP's first lap after its VSC."""
    return any(bool(row.get(flag)) for flag in ("is_sc", "is_vsc", "is_red"))


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
        previous: dict[str, object] | None = None
        for row, window in zip(rows, windows, strict=True):
            value = row["lap_time_fuel_corrected_s"]
            after_neutral = previous is not None and _was_neutralized(previous)
            previous = row
            if value is None or _is_context_excluded(row) or after_neutral:
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
                        "position": _as_int(row.get("position")),
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
                            "position": _as_int(row.get("position")),
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


_NEUTRAL_STATUSES: frozenset[str] = frozenset({"sc", "vsc", "red"})
"""Track statuses under which nobody may overtake — a position change on
such a lap is the pit lane or a retirement reshuffling the order."""


@dataclass(frozen=True, slots=True)
class _LapRow:
    driver: str
    lap_number: int
    position: int | None
    lap_start_utc: datetime | None
    lap_end_utc: datetime | None
    lap_time_s: float | None
    """`None` for an untimed lap. Some laps a car did complete carry no
    time (a race's first lap live, gaps in the archive's timing), so its
    end time is an estimate: only a timed lap is proof the car crossed the
    line when it says."""
    is_pit_in: bool
    is_pit_out: bool
    track_status: str | None
    is_sc: bool
    is_vsc: bool
    is_red: bool

    @property
    def pitting(self) -> bool:
        """An in- or out-lap: the car spent part of it in the pit lane."""
        return self.is_pit_in or self.is_pit_out

    @property
    def neutralized(self) -> bool:
        return self.track_status in _NEUTRAL_STATUSES or self.is_sc or self.is_vsc or self.is_red


class _LapTable:
    """The lap frame indexed the two ways the race-level detectors need it:
    by (driver, lap) and by lap. Built from the ANNOTATED laps, so the
    clean-lap filter's safety-car flags ride along with each row."""

    def __init__(self, laps: pl.DataFrame) -> None:
        self._rows: dict[tuple[str, int], _LapRow] = {}
        self._by_lap: dict[int, dict[str, _LapRow]] = defaultdict(dict)
        self._by_driver: dict[str, list[_LapRow]] = defaultdict(list)
        self._by_end: dict[datetime, list[_LapRow]] = defaultdict(list)
        columns = set(laps.columns)
        for raw in laps.to_dicts():
            if raw.get("lap_number") is None or raw.get("driver") is None:
                continue
            row = _LapRow(
                driver=str(raw["driver"]),
                lap_number=int(raw["lap_number"]),
                position=_as_int(raw.get("position")),
                lap_start_utc=raw.get("lap_start_utc"),
                lap_end_utc=raw.get("lap_end_utc"),
                lap_time_s=raw.get("lap_time_s"),
                is_pit_in=bool(raw.get("is_pit_in")),
                is_pit_out=bool(raw.get("is_pit_out")),
                track_status=raw.get("track_status"),
                is_sc="is_sc" in columns and bool(raw.get("is_sc")),
                is_vsc="is_vsc" in columns and bool(raw.get("is_vsc")),
                is_red="is_red" in columns and bool(raw.get("is_red")),
            )
            self._rows[(row.driver, row.lap_number)] = row
            self._by_lap[row.lap_number][row.driver] = row
            self._by_driver[row.driver].append(row)
            if row.lap_end_utc is not None:
                self._by_end[row.lap_end_utc].append(row)
        for rows in self._by_driver.values():
            rows.sort(key=lambda row: row.lap_number)
        self.max_lap = max(self._by_lap, default=0)

    @property
    def drivers(self) -> list[str]:
        return sorted(self._by_driver, key=lambda d: (len(d), d))

    def row(self, driver: str, lap_number: int) -> _LapRow | None:
        return self._rows.get((driver, lap_number))

    def lap(self, lap_number: int) -> dict[str, _LapRow]:
        return self._by_lap.get(lap_number, {})

    def driver_laps(self, driver: str) -> list[_LapRow]:
        return self._by_driver.get(driver, [])

    def position(self, driver: str, lap_number: int) -> int | None:
        row = self.row(driver, lap_number)
        return row.position if row is not None else None

    def leader(self, lap_number: int) -> _LapRow | None:
        """The race leader's row for `lap_number` — the car whose lap N is
        the race's lap N."""
        placed = [row for row in self.lap(lap_number).values() if row.position is not None]
        return min(placed, key=lambda row: row.position or 0, default=None)

    def order(self, lap_number: int) -> list[str]:
        """Drivers in classification order at the end of `lap_number`."""
        placed = [row for row in self.lap(lap_number).values() if row.position is not None]
        return [row.driver for row in sorted(placed, key=lambda row: row.position or 0)]

    def ending_at(self, when: datetime) -> _LapRow | None:
        """The lap that ended at exactly `when`, preferring one with a
        recorded position (untimed rows without one can share an end
        time)."""
        rows = self._by_end.get(when, [])
        placed = [row for row in rows if row.position is not None]
        return (placed or rows or [None])[0]


def _with_reveal(anomaly: Anomaly, table: _LapTable) -> Anomaly:
    """Stamp `anomaly` with the lap whose completion made it knowable:
    `reveal_driver`/`reveal_lap`, the (driver, lap) that ended at exactly
    `anomaly_time`.

    A client that gates a whole-session story on lap completion (the race
    story panel, which reads REST rather than the paced WebSocket) needs
    this, not `lap_number`: a safety car is numbered with the lap it began
    on but describes everything up to its restart, and a pit stop is
    numbered with its in-lap but reports what its out-lap cost. Gating on
    `lap_number` showed both laps early."""
    row = table.ending_at(anomaly.anomaly_time)
    if row is None:
        return anomaly
    return replace(
        anomaly,
        summary={**anomaly.summary, "reveal_driver": row.driver, "reveal_lap": row.lap_number},
    )


def _front_weight(position: int | None, params: AnomalyParams) -> float:
    """`1 + lead_bonus / position`: the same front-of-field weighting every
    race-level kind uses, so a P1/P2 moment outranks a P18/P19 one."""
    if position is None:
        return 1.0
    return 1.0 + params.position_change_lead_bonus / max(position, 1)


def _latest(times: Sequence[datetime | None]) -> datetime | None:
    present = [time for time in times if time is not None]
    return max(present) if present else None


def _detect_position_change(table: _LapTable, params: AnomalyParams) -> list[Anomaly]:
    """POSITION_CHANGE: an on-track overtake, never a pit-cycle reshuffle.

    For every lap, each car that gained places is credited only with the
    cars it moved ahead of that were themselves racing that lap: not on an
    in- or out-lap, and present in the order both before and after (a car
    that stopped is simply missing). A neutralized lap counts for nobody.
    One car losing `multi_place_loss_min_places` or more to cars on track in
    a single lap is reported once, from its side."""
    anomalies: list[Anomaly] = []
    for lap_number in range(2, table.max_lap + 1):
        before = table.lap(lap_number - 1)
        after = table.lap(lap_number)
        racing = [
            driver
            for driver, row in after.items()
            if row.position is not None
            and driver in before
            and before[driver].position is not None
            and not row.neutralized
        ]

        passed_by: dict[str, list[str]] = {}
        for gainer in racing:
            was, now = before[gainer].position, after[gainer].position
            assert was is not None and now is not None
            if now >= was or before[gainer].pitting or after[gainer].pitting:
                continue
            passed = [
                other
                for other in racing
                if other != gainer
                and not after[other].pitting
                and (before[other].position or 0) < was
                and (after[other].position or 0) > now
            ]
            if passed:
                passed_by[gainer] = sorted(passed, key=lambda d: before[d].position or 0)

        passers_of: dict[str, list[str]] = defaultdict(list)
        for gainer, passed in passed_by.items():
            for other in passed:
                passers_of[other].append(gainer)
        big_losers = {
            loser
            for loser, passers in passers_of.items()
            if len(passers) >= params.multi_place_loss_min_places
        }

        for loser in sorted(big_losers, key=lambda d: before[d].position or 0):
            passers = sorted(passers_of[loser], key=lambda d: after[d].position or 0)
            loss = _position_change_anomaly(
                driver=loser,
                rival=None,
                others=passers,
                gained=False,
                lap_number=lap_number,
                from_position=before[loser].position or 0,
                to_position=after[loser].position or 0,
                anomaly_time=_latest([after[d].lap_end_utc for d in (loser, *passers)]),
                params=params,
            )
            if loss is not None:
                anomalies.append(loss)
        for gainer, passed in passed_by.items():
            on_track = [other for other in passed if other not in big_losers]
            if not on_track:
                continue
            gain = _position_change_anomaly(
                driver=gainer,
                rival=on_track[0],
                others=on_track,
                gained=True,
                lap_number=lap_number,
                from_position=before[gainer].position or 0,
                to_position=after[gainer].position or 0,
                anomaly_time=_latest([after[d].lap_end_utc for d in (gainer, *on_track)]),
                params=params,
            )
            if gain is not None:
                anomalies.append(gain)
    return anomalies


def _position_change_anomaly(
    *,
    driver: str,
    rival: str | None,
    others: Sequence[str],
    gained: bool,
    lap_number: int,
    from_position: int,
    to_position: int,
    anomaly_time: datetime | None,
    params: AnomalyParams,
) -> Anomaly | None:
    """`others` is every car passed (for a gain) or every car that passed
    (for a multi-place loss), best-placed first; `rival` is the one a
    sentence names, the highest-placed car passed. `None` when no lap end
    time anchors the moment."""
    if anomaly_time is None:
        return None
    places = len(others)
    best_position = min(to_position, from_position)
    seconds_equivalent = (
        places * params.position_change_seconds_per_place * _front_weight(best_position, params)
    )
    names = ",".join(others)
    reason = (
        f"passed {names} on track on lap {lap_number} (P{from_position} -> P{to_position})"
        if gained
        else f"lost {places} places on track on lap {lap_number} "
        f"(P{from_position} -> P{to_position}), passed by {names}"
    )
    return Anomaly(
        kind=AnomalyKind.POSITION_CHANGE,
        lap_number=lap_number,
        anomaly_time=anomaly_time,
        drivers=(driver, rival) if rival is not None else (driver,),
        magnitude=float(places),
        salience=_salience(seconds_equivalent, params),
        summary={
            "from_position": from_position,
            "to_position": to_position,
            "places": places,
            "gained": gained,
            "rival": rival,
            "passed": names if gained else None,
            "passed_by": None if gained else names,
        },
        reasons=(reason,),
    )


# ---------------------------------------------------------------------------
# pit anomaly
# ---------------------------------------------------------------------------


def _detect_pit_anomaly(
    session_frames: SessionFrames,
    kernel_report: KernelReport,
    table: _LapTable,
    params: AnomalyParams,
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
        # A penalty served at this stop was announced some time during the
        # stint before it, so race control is searched from the stint start.
        stint_start = next(
            (
                row.lap_start_utc
                for row in reversed(table.driver_laps(sample.driver))
                if row.lap_number < sample.in_lap and (row.is_pit_out or row.lap_number == 1)
            ),
            None,
        )
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
                    "position": table.position(sample.driver, sample.in_lap),
                    "window_start_utc": stint_start.isoformat() if stint_start else None,
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
    session_frames: SessionFrames,
    kernel_report: KernelReport,
    table: _LapTable,
    params: AnomalyParams,
) -> list[Anomaly]:
    """DEG_CLIFF: a stint whose latest clean laps MEASURABLY ran slower than
    the straight line through its earlier clean laps predicts — the tyres
    falling away faster than they had been.

    Measured, not modelled: the tail's average residual against that line,
    fuel-corrected, is the number reported. The quadratic coefficient
    `fit_degradation` estimates comes from a separate fit with its own
    intercept and slope, and multiplying it by tyre age squared claimed a
    7.4s-a-lap cliff for the 2024 Miami GP's Ocon, whose laps 13-21 never
    moved by more than a second. Anchored at the tail's last lap, so a live
    race finds the cliff as it happens and every lap it uses is in the past."""
    laps = kernel_report.fuel_corrected_laps
    if laps.is_empty() or "lap_time_fuel_corrected_s" not in laps.columns:
        return []
    fits = {(fit.driver, fit.stint_number): fit for fit in kernel_report.degradation_fits}
    tail_n = params.deg_cliff_tail_laps

    anomalies: list[Anomaly] = []
    for (driver_value, stint_value), group in laps.group_by(
        ["driver", "stint_number"], maintain_order=True
    ):
        driver, stint = str(driver_value), _as_int(stint_value)
        rows = [
            row
            for row in group.sort("lap_number").to_dicts()
            if isinstance(row.get("lap_time_fuel_corrected_s"), int | float)
            and isinstance(row.get("tyre_age"), int)
            and row.get("lap_end_utc") is not None
        ]
        if len(rows) < max(params.deg_cliff_min_laps, tail_n + 3):
            continue
        compound = str(rows[-1].get("compound") or "").lower()
        if compound not in _SLICKS:
            continue  # a wet tyre's pace follows the track drying, not its own wear
        early, tail = rows[:-tail_n], rows[-tail_n:]
        x = np.asarray([row["tyre_age"] for row in early], dtype=float)
        y = np.asarray([row["lap_time_fuel_corrected_s"] for row in early], dtype=float)
        if np.ptp(x) == 0:
            continue
        slope = float(np.polyfit(x, y, 1)[0])
        if slope < params.deg_cliff_min_earlier_rate_s_per_lap:
            continue  # the car was getting quicker: track evolution, not wear
        before = [
            float(row["lap_time_fuel_corrected_s"])
            for row in early[-params.deg_cliff_reference_laps :]
        ]
        after = [float(row["lap_time_fuel_corrected_s"]) for row in tail]
        extra_time_s = float(np.mean(after) - np.mean(before))
        if extra_time_s < params.deg_cliff_min_extra_time_s or min(after) <= max(before):
            continue

        last = tail[-1]
        lap_number = int(last["lap_number"])
        fit = fits.get((driver, stint)) if stint is not None else None
        salience = _salience(extra_time_s, params)
        reasons = [
            f"the last {tail_n} clean laps of stint {stint} ({last.get('compound')}) ran "
            f"{extra_time_s:.1f}s a lap slower than the {len(before)} clean laps before them"
        ]
        if fit is not None and fit.reliability is Reliability.UNRELIABLE:
            salience *= params.unreliable_fit_derate
            reasons.append("underlying degradation fit is unreliable — treat this cautiously")

        anomalies.append(
            Anomaly(
                kind=AnomalyKind.DEG_CLIFF,
                lap_number=lap_number,
                anomaly_time=last["lap_end_utc"],
                drivers=(driver,),
                magnitude=extra_time_s,
                salience=salience,
                summary={
                    "stint_number": stint,
                    "compound": last.get("compound"),
                    "tyre_age_at_end": int(last["tyre_age"]),
                    "extra_time_s": extra_time_s,
                    "earlier_rate_s_per_lap": slope,
                    "reference_laps": len(before),
                    "tail_laps": tail_n,
                    "fit_reliability": fit.reliability.value if fit is not None else None,
                    "quadratic_term_s_per_lap2": (
                        fit.quadratic_term_s_per_lap2 if fit is not None else None
                    ),
                    "position": table.position(driver, lap_number),
                    # The last laps before the flag are often slow by choice
                    # (lifting and coasting, saving the car), not the tyres.
                    "ends_at_finish": kernel_report.total_laps is not None
                    and lap_number >= kernel_report.total_laps - 1,
                },
                reasons=tuple(reasons),
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# undercut resolved
# ---------------------------------------------------------------------------


def _detect_undercut_resolved(
    session_frames: SessionFrames,
    kernel_report: KernelReport,
    table: _LapTable,
    params: AnomalyParams,
) -> list[Anomaly]:
    """UNDERCUT_RESOLVED: a real stop-pair duel this session's own
    walk-forward prediction spoke to has resolved (both cars completed
    their post-stop lap) — scored against what actually happened exactly
    as `f1_kernel.undercut_eval`'s own backtest does, reused here per
    session rather than re-derived.

    Salience rests on whether the ORDER changed, weighted towards the front
    of the field. It used to be the realized margin, which ranked the 2024
    Bahrain GP's least interesting duels first: overcut attempts the
    attacker lost by 34-68 s, the cars never close and the order never in
    question."""
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
        order_changed = bool(outcome.actual_ahead)
        attacker_position = table.position(outcome.attacker, outcome.decision_lap)
        defender_position = table.position(outcome.defender, outcome.decision_lap)
        base = (
            params.undercut_order_change_seconds
            if order_changed
            else params.undercut_order_held_seconds
        )
        seconds_equivalent = base * weight * _front_weight(defender_position, params)

        # The LATER of the two cars' outcome laps: the verdict says where
        # both cars were, so it is not known until both have crossed.
        anomaly_time = _latest(
            [
                lap_end_utc_by_key.get((outcome.attacker, outcome.truth_lap)),
                lap_end_utc_by_key.get((outcome.defender, outcome.truth_lap)),
            ]
        )
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
                    "order_changed": order_changed,
                    "gap_at_decision_s": outcome.gap_at_decision_s,
                    "attacker_stop_lap": outcome.attacker_stop_lap,
                    "defender_stop_lap": outcome.defender_stop_lap,
                    "attacker_position": attacker_position,
                    "defender_position": defender_position,
                },
                reasons=tuple(reasons),
            )
        )
    return anomalies


# ---------------------------------------------------------------------------
# race landmarks: start, neutralizations, retirements, finish
# ---------------------------------------------------------------------------


def _distinct(drivers: Sequence[str | None]) -> tuple[str, ...]:
    """`drivers` with `None`s and repeats dropped, first occurrence kept."""
    kept: list[str] = []
    for driver in drivers:
        if driver is not None and driver not in kept:
            kept.append(driver)
    return tuple(kept)


def _changes_text(changes: Sequence[tuple[str, int]]) -> str:
    """`driver:places` pairs, comma-joined: flat enough for a summary value."""
    return ",".join(f"{driver}:{places}" for driver, places in changes)


def _detect_race_start(
    table: _LapTable, grid: Mapping[str, int] | None, params: AnomalyParams
) -> list[Anomaly]:
    """RACE_START: lap 1 against the grid. Anchored when the LAST car
    completes lap 1, so every position it reports is already known then."""
    if not grid:
        return []
    lap1 = {driver: row for driver, row in table.lap(1).items() if row.position is not None}
    anchor = _latest([row.lap_end_utc for row in lap1.values()])
    if not lap1 or anchor is None:
        return []
    leader = table.order(1)[0]
    pole = min(grid, key=lambda driver: (grid[driver], driver))
    changes = {
        driver: grid[driver] - (row.position or 0) for driver, row in lap1.items() if driver in grid
    }

    def by_size(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
        return sorted(items, key=lambda item: (-item[1], lap1[item[0]].position or 0))[:3]

    gainers = by_size([(d, c) for d, c in changes.items() if c >= params.start_min_places])
    losers = by_size([(d, -c) for d, c in changes.items() if -c >= params.start_min_places])
    biggest = max((abs(change) for change in changes.values()), default=0)

    reasons = [
        f"{leader} led after lap 1"
        + (f", from P{grid[leader]} on the grid" if leader in grid and leader != pole else "")
    ]
    reasons.extend(f"{d} gained {n} places on lap 1" for d, n in gainers)
    reasons.extend(f"{d} lost {n} places on lap 1" for d, n in losers)
    return [
        Anomaly(
            kind=AnomalyKind.RACE_START,
            lap_number=1,
            anomaly_time=anchor,
            drivers=_distinct([leader, pole, *(d for d, _ in gainers), *(d for d, _ in losers)]),
            magnitude=float(biggest),
            salience=_salience(params.race_start_seconds, params),
            summary={
                "leader": leader,
                "pole": pole,
                "lead_changed": leader != pole,
                "leader_grid": grid.get(leader),
                "gainers": _changes_text(gainers),
                "losers": _changes_text(losers),
            },
            reasons=tuple(reasons),
        )
    ]


_SEVERITY: dict[str, int] = {"vsc": 1, "sc": 2, "red": 3}
_STATUS_LABEL: dict[str, str] = {"vsc": "virtual safety car", "sc": "safety car", "red": "red flag"}


def _lap_status(row: _LapRow) -> str | None:
    if row.is_red or row.track_status == "red":
        return "red"
    if row.is_sc or row.track_status == "sc":
        return "sc"
    if row.is_vsc or row.track_status == "vsc":
        return "vsc"
    return None


@dataclass(frozen=True, slots=True)
class _Period:
    first: int
    last: int
    status: str
    """The most severe status the period reached."""
    opening: str
    """The status it began with — a safety car that became a red flag
    opens with `sc` and has `status` `red`."""
    worst_from: int
    """The lap the most severe status began."""


def _neutral_periods(table: _LapTable) -> list[_Period]:
    """Every neutralized run of the LEADER's laps that a green lap has since
    closed. A period still running (live, or a race that finished behind the
    safety car) is not over, so it is not reported yet."""
    periods: list[_Period] = []
    start: int | None = None
    opening = worst = ""
    worst_from = 0
    for lap_number in range(1, table.max_lap + 1):
        leader = table.leader(lap_number)
        status = _lap_status(leader) if leader is not None else None
        if status is not None:
            if start is None:
                start, opening, worst, worst_from = lap_number, status, status, lap_number
            elif _SEVERITY[status] > _SEVERITY[worst]:
                worst, worst_from = status, lap_number
            continue
        if start is not None and leader is not None:
            periods.append(_Period(start, lap_number - 1, worst, opening, worst_from))
        start = None
    return periods


def _retired_around(table: _LapTable, retirements: Sequence[Anomaly], first: int) -> list[str]:
    """Cars whose race ended on lap `first` or the lap before (the lap a
    car stopped on is the one after its last completed lap): what a
    neutralization starting on `first` was most likely called for. Measured
    from the lap frame, so it never rests on which race-control message
    happened to be sent first (the 2024 Monaco GP noted a Gasly/Ocon
    contact before the crash that actually stopped the race).

    Taken from the retirements the detector found, so a car is named here
    only when it would get a retirement card: live, judging by the leader's
    lap count alone named a lapped car (the 2026 Singapore GP's Bottas, three
    laps down and still running) as the reason for a VSC. And only when the
    race went on at least two more laps: a car that stopped on the last lap
    keeps its own card (the 2024 Australian GP's Russell, whose crash
    brought out a VSC as the race ended)."""
    retired: list[tuple[float, str]] = []
    for anomaly in retirements:
        last = anomaly.summary.get("last_completed_lap")
        if not isinstance(last, int):
            continue
        stopped = last + 1
        if first - 1 <= stopped <= first and table.max_lap > stopped + 2:
            retired.append((anomaly.magnitude, anomaly.drivers[0]))
    return [driver for _, driver in sorted(retired)]


def _detect_neutralizations(
    table: _LapTable,
    grid: Mapping[str, int] | None,
    retirements: Sequence[Anomaly],
    params: AnomalyParams,
) -> list[Anomaly]:
    """NEUTRALIZATION: each safety car / VSC / red flag, reported at the end
    of the first green lap after it, with what it did to the order: who
    pitted under it and who gained most between the lap before it and its
    last lap, and which cars' races ended as it began."""
    anomalies: list[Anomaly] = []
    for period in _neutral_periods(table):
        first, last, status = period.first, period.last, period.status
        retired = _retired_around(table, retirements, first)
        restart = table.leader(last + 1)
        if restart is None or restart.lap_end_utc is None:
            continue
        if first > 1:
            before = {d: row.position for d, row in table.lap(first - 1).items() if row.position}
        else:
            before = dict(grid or {})
        after = {d: row.position for d, row in table.lap(last).items() if row.position}
        pitted = sorted(
            {
                row.driver
                for lap_number in range(first, last + 1)
                for row in table.lap(lap_number).values()
                if row.is_pit_in
            },
            key=lambda d: (after.get(d) or 99, d),
        )
        gains = {d: before[d] - after[d] for d in after if d in before}
        best_gainer = min(
            (d for d, gain in gains.items() if gain > 0),
            key=lambda d: (-gains[d], after[d]),
            default=None,
        )
        leader_before = min(before, key=lambda d: before[d], default=None)
        leader_after = min(after, key=lambda d: after[d], default=None)
        opening = table.leader(first)
        window_start = opening.lap_start_utc if opening is not None else None

        label = _STATUS_LABEL[status]
        if period.opening != status:
            reasons = [
                f"{_STATUS_LABEL[period.opening]} from lap {first}, then a {label} from lap "
                f"{period.worst_from}, to lap {last}"
            ]
        else:
            reasons = [f"{label} from lap {first} to lap {last}"]
        if pitted:
            reasons.append(f"pitted under it: {', '.join(pitted)}")
        anomalies.append(
            Anomaly(
                kind=AnomalyKind.NEUTRALIZATION,
                lap_number=first,
                anomaly_time=restart.lap_end_utc,
                drivers=_distinct([leader_before, leader_after, best_gainer, *retired, *pitted]),
                magnitude=float(last - first + 1),
                salience=_salience(params.neutralization_seconds.get(status, 4.0), params),
                summary={
                    "status": status,
                    "opening_status": period.opening,
                    "worst_from_lap": period.worst_from,
                    "start_lap": first,
                    "end_lap": last,
                    "laps": last - first + 1,
                    "pitted": ",".join(pitted),
                    "retired": ",".join(retired),
                    "leader_before": leader_before,
                    "leader_after": leader_after,
                    "best_gainer": best_gainer,
                    "best_gain": gains.get(best_gainer) if best_gainer is not None else None,
                    "window_start_utc": window_start.isoformat() if window_start else None,
                },
                reasons=tuple(reasons),
            )
        )
    return anomalies


def _typical_lap_s(rows: Sequence[_LapRow]) -> float | None:
    """A car's usual lap time: the median of its recorded laps."""
    times = sorted(row.lap_time_s for row in rows if row.lap_time_s is not None)
    return times[len(times) // 2] if times else None


def _laps_down(table: _LapTable, at: datetime | None, last: int) -> int:
    """How many laps the race leader had completed beyond lap `last` by
    `at`: a car's deficit when it crossed the line at `at`."""
    if at is None:
        return 0
    led = max(
        (
            lap
            for lap in range(last + 1, table.max_lap + 1)
            if (row := table.leader(lap)) is not None
            and row.lap_end_utc is not None
            and row.lap_end_utc <= at
        ),
        default=last,
    )
    return led - last


def _stint_starts(stints: pl.DataFrame) -> set[tuple[str, int]]:
    """(driver, first lap) of every stint: a car that left the pits has a
    stint starting on its out-lap, completed or not."""
    if stints.is_empty():
        return set()
    return {
        (str(row["driver"]), int(row["start_lap"]))
        for row in stints.select("driver", "start_lap").to_dicts()
        if row["driver"] is not None and row["start_lap"] is not None
    }


def _detect_retirements(
    table: _LapTable,
    grid: Mapping[str, int] | None,
    total_laps: int | None,
    stint_starts: set[tuple[str, int]],
    params: AnomalyParams,
) -> list[Anomaly]:
    """RETIREMENT: a car that never completed a lap the cars behind it did.

    A lapped finisher is never caught by this (the cars behind it are lapped
    too and stop at the same flag), and a car that crashes on the final lap
    is. Anchored when the LAST of those cars behind completes that lap, by
    which time the car is unmistakably gone. A car that sat in the garage
    for a repair longer than that and then rejoined would be misread; the
    lap frame cannot tell the two apart.

    Only laps with a recorded position count as completed. The lap a car
    stopped on has no row at all, live or replayed (the feed never counts
    it, and `f1_historical_adapter` drops the archive's untimed row for
    it), so a car on the grid that completed nothing stopped on lap 1: the
    2024 Monaco GP's four lap-1 crashers. A replay's grid leaves out the
    cars the results say did not start (the 2024 São Paulo GP's Albon and
    Stroll); live, a car that never took the start may be on the grid the
    feed reported and would be read as stopping on lap 1. The position
    reported is where the car was RACING: when its last lap was an in-lap
    or a sudden drop (a car limping to the pits loses places on the way),
    it is the lap before.

    The last car on the road has nobody behind it: it counts as stopped once
    any car ahead has gone three laps further than its own deficit (the laps
    the leader had already completed beyond it when it last crossed the
    line), by which time even a lapped car would have completed one.

    While the race is still running, a car behind must have gone TWO laps
    further, not one. Watching live, a car in a long pit stop has also not
    completed a lap the cars passing it have: replaying the 2024 Miami GP lap
    by lap, as the live hub does, announced Verstappen (lap 23) and Sainz and
    Piastri (lap 27) as retired, each only pitting, and a live card can't be
    withdrawn. No stop costs a whole extra lap. Once the leader has completed
    the race distance, one lap settles it again, so a car that stops on the
    final lap still counts (and a finished race reads exactly as before)."""
    winner = table.leader(total_laps) if total_laps is not None else None
    race_over = winner is not None
    flag_at = winner.lap_end_utc if winner is not None else None
    laps_on = 1 if race_over else 2
    # How far the frame reaches: live, the latest crossing seen so far.
    horizon = _latest([row.lap_end_utc for d in table.drivers for row in table.driver_laps(d)])
    anomalies: list[Anomaly] = []
    on_grid: list[str] = list(grid) if grid else []
    drivers = sorted({*table.drivers, *on_grid}, key=lambda d: (len(d), d))
    for driver in drivers:
        rows = table.driver_laps(driver)
        completed = [row for row in rows if row.position is not None]
        last_row: _LapRow | None = None
        if completed:
            last_row = completed[-1]
            last = last_row.lap_number
            previous = completed[-2] if len(completed) > 1 else None
            limping = previous is not None and (
                last_row.pitting
                or (last_row.position or 0) - (previous.position or 0)
                >= params.multi_place_loss_min_places
            )
            reference = previous if limping and previous is not None else last_row
            position = reference.position
            since = last_row.lap_end_utc
            behind = [
                other
                for other, row in table.lap(reference.lap_number).items()
                if other != driver
                and row.position is not None
                and position is not None
                and row.position > position
            ]
        elif grid and driver in grid:
            last, position, since = 0, grid[driver], None
            behind = [other for other, slot in grid.items() if slot > grid[driver]]
        else:
            continue
        if position is None:
            continue
        if (
            flag_at is not None
            and last_row is not None
            and last_row.lap_time_s is not None
            and since is not None
            and since >= flag_at
        ):
            # A timed crossing after the winner's: took the chequered flag.
            continue
        crossed = _latest([row.lap_end_utc for row in completed if row.lap_time_s is not None])
        if race_over and crossed is not None and horizon is not None:
            # After the flag one lap of evidence is enough only once the car
            # is overdue, counted from its last timed crossing: a car passed
            # on the last lap is still coming
            # (`AnomalyParams.retirement_overdue_laps`).
            typical = _typical_lap_s(completed)
            if typical is not None and horizon < crossed + timedelta(
                seconds=typical * params.retirement_overdue_laps
            ):
                continue
        went_on = [table.row(other, last + laps_on) for other in behind]
        anchor = _latest(
            [row.lap_end_utc for row in went_on if row is not None and row.position is not None]
        )
        if anchor is None:
            # Counted from the car's own deficit: a car two laps down is
            # already three behind each time the leader crosses the line,
            # and live that called six of the 2024 Monaco GP's lapped
            # finishers retired.
            down = _laps_down(table, since, last)
            further = [
                row
                for other in table.drivers
                if other != driver
                and (row := table.row(other, last + down + 3)) is not None
                and row.position is not None
            ]
            anchor = min((row.lap_end_utc for row in further if row.lap_end_utc), default=None)
        if anchor is None:
            continue
        anomalies.append(
            Anomaly(
                kind=AnomalyKind.RETIREMENT,
                lap_number=last + 1,
                anomaly_time=anchor,
                drivers=(driver,),
                magnitude=float(position),
                salience=_salience(
                    params.retirement_seconds * _front_weight(position, params), params
                ),
                summary={
                    "position": position,
                    "last_completed_lap": last,
                    # Came into the pits on its last lap and never
                    # completed another. Not proof it stayed there: a car
                    # that left again started a stint on its out-lap, but
                    # the archive can lose that lap entirely (the 2024 São
                    # Paulo GP's Colapinto pitted on lap 30 and crashed on
                    # lap 31, bringing out the red flag).
                    "in_pits": last_row is not None
                    and last_row.is_pit_in
                    and table.row(driver, last + 1) is None
                    and (driver, last + 1) not in stint_starts,
                    "window_start_utc": since.isoformat() if since else None,
                },
                reasons=(
                    f"{driver} stopped while running P{position}: never completed lap "
                    f"{last + 1}, which the cars behind did",
                ),
            )
        )
    return anomalies


def _detect_race_finish(
    table: _LapTable,
    total_laps: int | None,
    grid: Mapping[str, int] | None,
    params: AnomalyParams,
) -> list[Anomaly]:
    """RACE_FINISH: the leader has completed the race distance. Reported as
    the order across the line, which is what the lap frame knows: a later
    penalty or disqualification is not in it."""
    if not total_laps:
        return []
    winner = table.leader(total_laps)
    if winner is None or winner.position != 1 or winner.lap_end_utc is None:
        return []
    podium = table.order(total_laps)[:3]
    final = table.lap(total_laps)
    anchor = _latest([final[d].lap_end_utc for d in podium])
    second = podium[1] if len(podium) > 1 else None
    margin: float | None = None
    won_at = winner.lap_end_utc
    second_at = final[second].lap_end_utc if second is not None else None
    if second_at is not None:
        margin = (second_at - won_at).total_seconds()
    if anchor is None:
        return []
    reasons = [f"{winner.driver} completed lap {total_laps} first"]
    if second is not None and margin is not None:
        reasons.append(f"{margin:.1f}s ahead of {second}")
    return [
        Anomaly(
            kind=AnomalyKind.RACE_FINISH,
            lap_number=total_laps,
            anomaly_time=anchor,
            drivers=tuple(podium),
            magnitude=max(margin or 0.0, 0.0),
            salience=_salience(params.race_finish_seconds, params),
            summary={
                "winner": winner.driver,
                "second": second,
                "third": podium[2] if len(podium) > 2 else None,
                "margin_s": margin,
                "winner_grid": grid.get(winner.driver) if grid else None,
                "laps": total_laps,
            },
            reasons=tuple(reasons),
        )
    ]


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


__all__ = ["detect_anomalies", "starting_grid"]
