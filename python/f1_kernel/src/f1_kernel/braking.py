"""Braking-point drift: the thing that originally looked like it needed a
camera, done from the brake channel every lap instead.

A driver's braking point for a corner creeps EARLIER (more distance before
the corner) as the tyres go off and the brakes fade — less mechanical grip
means the car cannot shed speed as late. That is a real, physical signal
sitting in F1's own telemetry; nobody needs to watch a video feed to find
it. This module has two stages, mirroring `f1_kernel.degradation`'s split
between "measure one thing" and "fit a trend across a stint":

- **`braking_points`** — for one driver's telemetry and one corner-mapped
  circuit, find the track distance at which the brake trace crosses
  `params.brake_on_threshold_pct` ON APPROACH to each corner, per lap.
  Noise is handled two ways: the brake trace is binned onto a distance grid
  (`params.braking_distance_bin_m`) before thresholding, and a crossing
  only counts once it stays "on" for `params.braking_min_sustained_m` — a
  single noisy stab does not register as a braking point. Returns a FRAME
  (one row per lap x corner), never silently dropping a corner a lap could
  not speak to: `known=False` says "not enough telemetry here", distinct
  from `brake_point_distance_m=None` with `known=True`, which says "enough
  telemetry, but no sustained braking event was found".
- **`braking_drift`** — per (driver, stint, corner), a robust (Theil-Sen)
  fit of `distance_before_corner_m` against tyre age, exactly the same
  method `fit_degradation` uses for lap time — and it reuses that module's
  `Reliability` verdict for the same reason: a 3-lap trend and a 15-lap
  trend are not the same claim. A positive `drift_m_per_lap` is the
  expected direction (braking earlier as the tyre ages); a fit can
  legitimately come back flat or negative on a stint too short to show
  wear, or a compound that does not fade much.

Corner windows are clipped so one corner's search cannot wander into the
previous corner's own braking/exit zone (`params.braking_approach_window_m`,
clipped further at the previous corner's own distance for corners close
together, e.g. a chicane). Telemetry is historical-mostly and often sparse
by CLAUDE.md's own framing — every failure mode here degrades to a
reduced-confidence result (`known=False`, a fit never attempted, or a
`thin`/`unreliable` verdict), never an exception.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import polars as pl

from f1_kernel._robust_stats import theilslopes as _theilslopes
from f1_kernel.circuit import Circuit, Corner
from f1_kernel.degradation import Reliability
from f1_kernel.params import KernelParams
from f1_kernel.schemas import Schema, empty_frame

_TELEMETRY_REQUIRED_COLUMNS = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "lap_number",
    "ts_utc",
    "lap_distance_m",
    "brake_pct",
)

_BRAKING_REQUIRED_COLUMNS = (
    "driver",
    "lap_number",
    "corner_number",
    "corner_letter",
    "known",
    "distance_before_corner_m",
)

_STINT_REQUIRED_COLUMNS = (
    "session_year",
    "session_round",
    "session_type",
    "driver",
    "stint_number",
    "compound",
    "start_lap",
    "end_lap",
    "tyre_age_at_start",
)

# ---------------------------------------------------------------------------
# braking points
# ---------------------------------------------------------------------------

BRAKING_FRAME_SCHEMA: Schema = {
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "lap_number": pl.Int32(),
    "corner_number": pl.Int32(),
    "corner_letter": pl.String(),
    "corner_distance_m": pl.Float64(),
    "brake_point_distance_m": pl.Float64(),
    "distance_before_corner_m": pl.Float64(),
    "n_samples_in_window": pl.Int32(),
    "known": pl.Boolean(),
}
"""One row per (lap, corner) a driver's telemetry could speak to.

`distance_before_corner_m` (`corner_distance_m - brake_point_distance_m`) is
the natural x-axis for a drift trend: positive and growing means braking
earlier. `n_samples_in_window` and `known` are the quality signal —
`known=False` means too little telemetry fell in the corner's approach
window to trust any verdict at all, which is different from `known=True`
with a null `brake_point_distance_m` (plenty of telemetry, but no sustained
braking event crossed the threshold)."""


def braking_points(
    telemetry: pl.DataFrame, circuit: Circuit | None, params: KernelParams
) -> pl.DataFrame:
    """Per lap, per mapped corner, the sustained brake-application point.

    `telemetry` is one driver's `TELEMETRY_FRAME_SCHEMA` frame (as built by
    `f1_kernel.telemetry_frame`). Degrades gracefully rather than raising:
    no circuit, no corners, or no usable telemetry all return an EMPTY
    frame with the right schema, never an exception — telemetry coverage is
    exactly the kind of thing that varies session to session.

    Pure and deterministic: the same telemetry, circuit and params always
    produce byte-identical rows in the same order.
    """
    _require_columns(telemetry, _TELEMETRY_REQUIRED_COLUMNS, "telemetry frame")
    if telemetry.is_empty() or circuit is None or not circuit.corners:
        return empty_frame(BRAKING_FRAME_SCHEMA)

    corners = sorted(circuit.corners, key=lambda corner: corner.distance_m)
    windows = _corner_windows(corners, params.braking_approach_window_m)

    usable = telemetry.filter(
        pl.col("lap_number").is_not_null()
        & pl.col("lap_distance_m").is_not_null()
        & pl.col("driver").is_not_null()
    )
    if usable.is_empty():
        return empty_frame(BRAKING_FRAME_SCHEMA)

    columns: dict[str, list[object]] = {name: [] for name in BRAKING_FRAME_SCHEMA}
    for (driver_value, lap_value), group in usable.group_by(
        ["driver", "lap_number"], maintain_order=True
    ):
        driver = str(driver_value)
        lap_number = int(lap_value)  # type: ignore[arg-type]
        ordered = group.sort("ts_utc")
        row0 = ordered.row(0, named=True)
        samples = [
            (float(distance), float(brake))
            for distance, brake in zip(ordered["lap_distance_m"], ordered["brake_pct"], strict=True)
            if distance is not None and brake is not None
        ]

        for corner, (window_start, window_end) in zip(corners, windows, strict=True):
            in_window = [(d, b) for d, b in samples if window_start <= d <= window_end]
            brake_point, n_samples = _detect_brake_point(in_window, window_start, params)
            known = n_samples >= params.braking_min_samples_in_window

            columns["session_year"].append(row0["session_year"])
            columns["session_round"].append(row0["session_round"])
            columns["session_type"].append(row0["session_type"])
            columns["driver"].append(driver)
            columns["lap_number"].append(lap_number)
            columns["corner_number"].append(corner.number)
            columns["corner_letter"].append(corner.letter)
            columns["corner_distance_m"].append(corner.distance_m)
            columns["brake_point_distance_m"].append(brake_point if known else None)
            columns["distance_before_corner_m"].append(
                corner.distance_m - brake_point if known and brake_point is not None else None
            )
            columns["n_samples_in_window"].append(n_samples)
            columns["known"].append(known)

    frame = pl.DataFrame(columns, schema=BRAKING_FRAME_SCHEMA)
    return frame.sort("driver", "lap_number", "corner_distance_m")


def _corner_windows(
    corners: Sequence[Corner], approach_window_m: float
) -> list[tuple[float, float]]:
    """`(window_start, window_end)` per corner, in the same order as
    `corners`. `window_end` is the corner itself; `window_start` reaches
    back `approach_window_m` but never past the previous corner's own
    distance, so two corners close together (a chicane) cannot have their
    braking zones mixed up."""
    windows: list[tuple[float, float]] = []
    previous_distance = 0.0
    for corner in corners:
        start = max(0.0, corner.distance_m - approach_window_m, previous_distance)
        windows.append((start, corner.distance_m))
        previous_distance = corner.distance_m
    return windows


def _detect_brake_point(
    in_window: list[tuple[float, float]], window_start: float, params: KernelParams
) -> tuple[float | None, int]:
    """`(brake_point_distance_m, n_samples)` for one lap's approach to one
    corner. `n_samples` counts every sample in the window regardless of
    brake state — the coverage signal `known` gates on. The point itself is
    the EARLIEST (furthest from the corner) run of consecutive on-bins at
    least `braking_min_sustained_m` long; a run that never reaches that
    length is a stab, not a braking point, and is ignored."""
    n_samples = len(in_window)
    if n_samples == 0:
        return None, 0

    bin_m = params.braking_distance_bin_m
    threshold = params.brake_on_threshold_pct
    on_bins: dict[int, float] = {}
    for distance, brake in in_window:
        if brake < threshold:
            continue
        index = int((distance - window_start) // bin_m)
        existing = on_bins.get(index)
        if existing is None or distance < existing:
            on_bins[index] = distance

    if not on_bins:
        return None, n_samples

    min_run_bins = max(1, math.ceil(params.braking_min_sustained_m / bin_m))
    sorted_indices = sorted(on_bins)
    run_start = sorted_indices[0]
    run_length = 1
    for previous, current in pairwise(sorted_indices):
        if current == previous + 1:
            run_length += 1
            continue
        if run_length >= min_run_bins:
            return on_bins[run_start], n_samples
        run_start = current
        run_length = 1
    if run_length >= min_run_bins:
        return on_bins[run_start], n_samples
    return None, n_samples


# ---------------------------------------------------------------------------
# braking drift
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrakingDrift:
    """One (driver, stint, corner)'s fitted braking-point trend."""

    session_year: int
    session_round: int
    session_type: str
    driver: str
    stint_number: int
    compound: str
    corner_number: int
    corner_letter: str | None
    drift_m_per_lap: float
    """Robust (Theil-Sen) slope of `distance_before_corner_m` against tyre
    age. Positive means the brake point moves EARLIER as the tyre ages (the
    expected direction on a stint with real wear); a fit can legitimately
    come back flat or negative on a short/low-deg stint."""
    drift_ci_low_m_per_lap: float
    drift_ci_high_m_per_lap: float
    """95% confidence interval on `drift_m_per_lap`, from Theil-Sen's own
    interval — the width `reliability` gates on."""
    intercept_m: float
    """Fitted `distance_before_corner_m` at tyre_age = 0 — the stint's
    baseline braking point, extrapolated one lap before the first sample."""
    n_laps_used: int
    reliability: Reliability


BRAKING_DRIFT_SCHEMA: Schema = {
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "stint_number": pl.Int32(),
    "compound": pl.String(),
    "corner_number": pl.Int32(),
    "corner_letter": pl.String(),
    "drift_m_per_lap": pl.Float64(),
    "drift_ci_low_m_per_lap": pl.Float64(),
    "drift_ci_high_m_per_lap": pl.Float64(),
    "intercept_m": pl.Float64(),
    "n_laps_used": pl.Int32(),
    "reliability": pl.String(),
}


def braking_drift(
    braking_frame: pl.DataFrame, stint_frame: pl.DataFrame, params: KernelParams
) -> list[BrakingDrift]:
    """One `BrakingDrift` per (driver, stint, corner) with at least two
    laps carrying a KNOWN braking point (`known=True` and a non-null
    `distance_before_corner_m`) inside that stint.

    Tyre age per lap is derived from `stint_frame` exactly the way
    `f1_kernel.frames` derives it (`lap_number - start_lap + 1 +
    tyre_age_at_start`), so a braking-point lap and its degradation
    counterpart always agree on how old the tyre was. Rows whose lap falls
    outside every known stint (or an unmapped/unknown braking point) are
    excluded from the fit rather than coerced into one — the same
    "exclude, do not invent" rule `fit_degradation` follows.

    Pure and deterministic: same frames and params in, identical fits out,
    ordered by `(driver, stint_number, corner_number, corner_letter)`.
    """
    _require_columns(braking_frame, _BRAKING_REQUIRED_COLUMNS, "braking frame")
    _require_columns(stint_frame, _STINT_REQUIRED_COLUMNS, "stint frame")
    if braking_frame.is_empty() or stint_frame.is_empty():
        return []

    usable = braking_frame.filter(
        pl.col("known") & pl.col("distance_before_corner_m").is_not_null()
    )
    if usable.is_empty():
        return []

    stint_ranges = _stint_ranges(stint_frame)
    session_by_driver = _session_identity_by_driver(stint_frame)

    grouped: dict[tuple[str, int, int, str | None], list[tuple[int, float]]] = defaultdict(list)
    compound_by_group: dict[tuple[str, int, int, str | None], str] = {}

    for row in usable.iter_rows(named=True):
        driver = str(row["driver"])
        ranges = stint_ranges.get(driver)
        if not ranges:
            continue
        stint = _find_stint(ranges, int(row["lap_number"]))
        if stint is None:
            continue
        tyre_age = int(row["lap_number"]) - stint.start_lap + 1 + stint.tyre_age_at_start
        key = (driver, stint.stint_number, int(row["corner_number"]), row["corner_letter"])
        grouped[key].append((tyre_age, float(row["distance_before_corner_m"])))
        compound_by_group[key] = stint.compound

    fits: list[BrakingDrift] = []
    for key in sorted(grouped, key=lambda k: (k[0], k[1], k[2], k[3] or "")):
        pairs = grouped[key]
        if len({age for age, _ in pairs}) < 2:
            continue
        x = np.asarray([age for age, _ in pairs], dtype=float)
        y = np.asarray([distance for _, distance in pairs], dtype=float)
        slope, intercept, ci_low, ci_high = _theilslopes(y, x, alpha=0.95)
        if not all(np.isfinite(v) for v in (slope, intercept, ci_low, ci_high)):
            continue

        driver, stint_number, corner_number, corner_letter = key
        session_year, session_round, session_type = session_by_driver[driver]
        fits.append(
            BrakingDrift(
                session_year=session_year,
                session_round=session_round,
                session_type=session_type,
                driver=driver,
                stint_number=stint_number,
                compound=compound_by_group[key],
                corner_number=corner_number,
                corner_letter=corner_letter,
                drift_m_per_lap=slope,
                drift_ci_low_m_per_lap=ci_low,
                drift_ci_high_m_per_lap=ci_high,
                intercept_m=intercept,
                n_laps_used=len(pairs),
                reliability=_reliability(len(pairs), ci_high - ci_low, params),
            )
        )
    return fits


def braking_drift_to_frame(fits: Sequence[BrakingDrift]) -> pl.DataFrame:
    """`braking_drift`'s output as a frame — one row per fit, for a
    backtest report or further aggregation."""
    if not fits:
        return pl.DataFrame(schema=BRAKING_DRIFT_SCHEMA)
    rows = [
        {
            "session_year": fit.session_year,
            "session_round": fit.session_round,
            "session_type": fit.session_type,
            "driver": fit.driver,
            "stint_number": fit.stint_number,
            "compound": fit.compound,
            "corner_number": fit.corner_number,
            "corner_letter": fit.corner_letter,
            "drift_m_per_lap": fit.drift_m_per_lap,
            "drift_ci_low_m_per_lap": fit.drift_ci_low_m_per_lap,
            "drift_ci_high_m_per_lap": fit.drift_ci_high_m_per_lap,
            "intercept_m": fit.intercept_m,
            "n_laps_used": fit.n_laps_used,
            "reliability": fit.reliability.value,
        }
        for fit in fits
    ]
    return pl.DataFrame(rows, schema=BRAKING_DRIFT_SCHEMA)


@dataclass(frozen=True, slots=True)
class _StintRange:
    stint_number: int
    compound: str
    start_lap: int
    end_lap: int
    tyre_age_at_start: int


def _stint_ranges(stint_frame: pl.DataFrame) -> dict[str, list[_StintRange]]:
    """Per-driver stints with a known `[start_lap, end_lap]`, sorted by
    start — a stint missing either bound cannot map a lap to a tyre age, so
    it is skipped rather than guessed."""
    by_driver: dict[str, list[_StintRange]] = defaultdict(list)
    for row in stint_frame.iter_rows(named=True):
        if row["start_lap"] is None or row["end_lap"] is None or row["compound"] is None:
            continue
        by_driver[str(row["driver"])].append(
            _StintRange(
                stint_number=int(row["stint_number"]),
                compound=str(row["compound"]),
                start_lap=int(row["start_lap"]),
                end_lap=int(row["end_lap"]),
                tyre_age_at_start=int(row["tyre_age_at_start"] or 0),
            )
        )
    for ranges in by_driver.values():
        ranges.sort(key=lambda r: r.start_lap)
    return dict(by_driver)


def _find_stint(ranges: Sequence[_StintRange], lap_number: int) -> _StintRange | None:
    for stint in ranges:
        if stint.start_lap <= lap_number <= stint.end_lap:
            return stint
    return None


def _session_identity_by_driver(stint_frame: pl.DataFrame) -> dict[str, tuple[int, int, str]]:
    identity: dict[str, tuple[int, int, str]] = {}
    for row in stint_frame.iter_rows(named=True):
        identity.setdefault(
            str(row["driver"]),
            (int(row["session_year"]), int(row["session_round"]), str(row["session_type"])),
        )
    return identity


def _reliability(n_laps: int, ci_width: float, params: KernelParams) -> Reliability:
    if n_laps < params.min_laps_for_braking_drift:
        return Reliability.THIN
    if ci_width > params.braking_drift_max_ci_width_m_per_lap:
        return Reliability.UNRELIABLE
    return Reliability.GOOD


def _require_columns(frame: pl.DataFrame, required: Sequence[str], what: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing required column(s) {missing}")


__all__ = [
    "BRAKING_DRIFT_SCHEMA",
    "BRAKING_FRAME_SCHEMA",
    "BrakingDrift",
    "braking_drift",
    "braking_drift_to_frame",
    "braking_points",
]
