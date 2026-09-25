"""The track outline: a real 2D lap shape derived from real car position
samples, never hand-drawn.

Position is a channel this project only ever observes LIVE
(`f1_domain.CarPosition`, fed by the feed's `Position.z` topic) or via
FastF1's own position telemetry (the same channel, replayed from F1's
official API for a past session) — `f1_backfill`'s historical parquet
tables carry no position data at all (DATA_CONTRACTS.md's historical-adapter
section), so an outline can never be built from `HistoricalSource`. This
module is the pure transform half of that build step: `derive_track_outline`
takes (x, y) points already in hand — from one representative lap of one
driver — and turns them into a stable, cacheable shape. It never opens a
file, a session or a network connection; `tools/build_circuits.py` (which
already loads a real FastF1 session for corners/DRS zones) is the caller
that has real points to hand it, and `Circuit.outline` is where the result
is cached per circuit, per CLAUDE.md's "calibrated where possible, never
guessed."

`points` are deliberately left in the SAME raw coordinate units the input
arrived in (the feed's own ~1/10 m units — see `f1_domain.CarPosition`) and
are NOT centered or rescaled to any canonical display range. That is the
point: a live `car_position` sample from the same session's own channel can
be plotted directly alongside these points with no extra transform, because
nothing here invented one. Fitting the shape to a screen (aspect-preserving
scale-to-canvas) is a display concern, not a data concern — Phase 3.4's
frontend (`$lib/track-map/geometry.fitOutlineToCanvas`) does that once, at
render time, and reuses the SAME resulting viewport for the outline and
every car marker so the two can never drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

# A closed lap needs enough samples to describe a shape at all; below this
# a "lap" is more likely a data glitch (a handful of stray samples) than a
# real position trace.
MIN_SOURCE_POINTS = 8

# Position samples arrive faster than the car moves relative to sampling
# noise; two samples closer than this (in raw feed units) are treated as
# one point so a stationary/slow-crawling car (pit lane, grid formation)
# cannot create zero-length segments that break arc-length resampling.
MIN_POINT_SPACING = 1e-6

# The feed's own position units are "the feed's own units (~1/10 m)" per
# `f1_domain.CarPosition`'s docstring; FastF1's telemetry X/Y/Z replay the
# same channel and share the same approximate scale. This is informational
# only (it feeds `width_m`/`height_m`; `points` stays in raw units).
DEFAULT_UNITS_PER_METRE = 10.0


class TrackOutline(BaseModel):
    """One circuit's real 2D lap shape, cached per circuit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[tuple[float, float]]
    """Closed polyline (`points[0] == points[-1]`) around one lap, evenly
    resampled by arc length. In the SAME raw coordinate units as the input
    position samples — NOT centered or rescaled — so a live `car_position`
    sample from this circuit plots directly alongside these points with no
    additional transform. See the module docstring for why."""
    width_m: float = Field(ge=0)
    height_m: float = Field(ge=0)
    """Bounding-box extent of the shape, in metres — approximate, since the
    source units themselves are approximate (see `DEFAULT_UNITS_PER_METRE`).
    Informational (e.g. a scale bar); `points` is what a renderer actually
    draws, fitted to its own canvas at display time."""
    source_points: int = Field(ge=0)
    """How many raw position samples fed the derivation, BEFORE resampling —
    a quality signal on the derivation itself, the same spirit as the
    kernel's own reliability verdicts elsewhere. Not a promise that this many
    points survived: a sparse or noisy source still produces `points` with
    exactly `target_points` entries."""


class PitLane(BaseModel):
    """One circuit's pit lane: an OPEN polyline from pit entry to pit exit,
    in the same raw units as `TrackOutline.points` (and every position
    sample), so a car in the pits plots on it with no transform.

    Without it the map drew only the racing line, and every car in the pit
    lane or its garage sat in empty space beside the track: in practice and
    qualifying that was a fifth to two thirds of all position samples."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[tuple[float, float]]
    length_m: float = Field(ge=0)
    source_stops: int = Field(ge=0)
    """How many real pit stops the shape was derived from."""


MIN_PIT_STOPS = 3
"""Fewer stops than this and a median path is just one car's path."""

# Consecutive samples closer than this (raw units, ~1 m) are one point: the
# car standing in its box, which says nothing about where the lane runs.
_PIT_POINT_SPACING = 10.0


def derive_pit_lane(
    traverses: Sequence[Sequence[tuple[float, float]]],
    *,
    target_points: int = 80,
    iterations: int = 3,
    units_per_metre: float = DEFAULT_UNITS_PER_METRE,
) -> PitLane:
    """The pit lane's centreline from real pit stops.

    Each traverse is one car's time-ordered samples from pit entry to pit
    exit. Every car swerves into its own box somewhere along the lane, so
    no single traverse is the lane; the MEDIAN of all of them is, because
    at any point along it most cars are driving straight through. The first
    shape is the pointwise median of every traverse resampled to the same
    number of points by arc length. Starting from any one traverse instead
    kept that car's own swerve: its samples were the only ones near it.
    Each refining round then assigns every sample to its nearest point on
    the shape, moves that point to the median of what it was given, and
    resamples. Raises `ValueError` with fewer than `MIN_PIT_STOPS` usable
    traverses."""
    paths = [
        np.asarray(_thin(traverse), dtype=float)
        for traverse in traverses
        if len(traverse) >= MIN_SOURCE_POINTS
    ]
    paths = [path for path in paths if len(path) >= MIN_SOURCE_POINTS]
    if len(paths) < MIN_PIT_STOPS:
        raise ValueError(
            f"need at least {MIN_PIT_STOPS} pit-lane traverses, got {len(paths)} usable"
        )
    shape = np.median(np.stack([_resample_open(path, target_points) for path in paths]), axis=0)
    shape = _resample_open(shape, target_points)
    samples = np.concatenate(paths)
    for _ in range(iterations):
        nearest = np.argmin(
            (samples[:, None, 0] - shape[None, :, 0]) ** 2
            + (samples[:, None, 1] - shape[None, :, 1]) ** 2,
            axis=1,
        )
        moved = shape.copy()
        for i in range(len(shape)):
            assigned = samples[nearest == i]
            if len(assigned) >= MIN_PIT_STOPS:
                moved[i] = np.median(assigned, axis=0)
        shape = _resample_open(moved, target_points)
    length = float(np.hypot(*np.diff(shape, axis=0).T).sum())
    return PitLane(
        points=[(float(x), float(y)) for x, y in shape],
        length_m=length / units_per_metre,
        source_stops=len(paths),
    )


def derive_track_outline_from_laps(
    laps: Sequence[Sequence[tuple[float, float]]],
    *,
    target_points: int = 200,
    iterations: int = 3,
    units_per_metre: float = DEFAULT_UNITS_PER_METRE,
) -> TrackOutline:
    """A racing line from several laps' samples, each a time-ordered trace
    that starts where the lap did (the timing line).

    The median-length lap is the first shape; each round assigns every
    sample of every lap to its nearest point on the shape, moves that point
    to the median of what it was given and resamples the closed loop by
    arc length. Laps start a sample or two apart and never at exactly the
    same place, so points are matched by nearness rather than by index.
    Raises `ValueError` without at least one usable lap."""
    paths = [np.asarray(_dedupe(lap), dtype=float) for lap in laps if len(lap) >= MIN_SOURCE_POINTS]
    if not paths:
        raise ValueError("need at least one lap of position samples to derive a racing line")
    lengths = [float(np.hypot(*np.diff(path, axis=0).T).sum()) for path in paths]
    reference = paths[int(np.argsort(lengths)[len(lengths) // 2])]
    shape = _resample_closed(reference, target_points)
    samples = np.concatenate(paths)
    for _ in range(iterations):
        nearest = np.argmin(
            (samples[:, None, 0] - shape[None, :, 0]) ** 2
            + (samples[:, None, 1] - shape[None, :, 1]) ** 2,
            axis=1,
        )
        moved = shape.copy()
        for i in range(len(shape)):
            assigned = samples[nearest == i]
            if len(assigned) >= min(2, len(paths)):
                moved[i] = np.median(assigned, axis=0)
        shape = _resample_closed(moved, target_points)
    outline = derive_track_outline(
        [(float(x), float(y)) for x, y in shape],
        target_points=target_points,
        units_per_metre=units_per_metre,
    )
    return outline.model_copy(update={"source_points": int(sum(len(path) for path in paths))})


def _resample_closed(points: np.ndarray, target_points: int) -> np.ndarray:
    """`target_points` points evenly spaced around the closed loop, the
    first one kept."""
    loop = np.vstack([points, points[:1]])
    steps: np.ndarray = np.hypot(*np.diff(loop, axis=0).T)
    cumulative: np.ndarray = np.concatenate([[0.0], np.cumsum(steps)])
    targets: np.ndarray = np.linspace(0.0, float(cumulative[-1]), target_points, endpoint=False)
    xs: np.ndarray = np.interp(targets, cumulative, loop[:, 0])
    ys: np.ndarray = np.interp(targets, cumulative, loop[:, 1])
    return np.column_stack([xs, ys])


def _thin(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    kept = [points[0]]
    for point in points[1:]:
        if _distance(kept[-1], point) >= _PIT_POINT_SPACING:
            kept.append(point)
    return kept


def _resample_open(points: np.ndarray, target_points: int) -> np.ndarray:
    """Evenly spaced by arc length, first and last point kept."""
    steps: np.ndarray = np.hypot(*np.diff(points, axis=0).T)
    cumulative: np.ndarray = np.concatenate([[0.0], np.cumsum(steps)])
    targets: np.ndarray = np.linspace(0.0, float(cumulative[-1]), target_points)
    xs: np.ndarray = np.interp(targets, cumulative, points[:, 0])
    ys: np.ndarray = np.interp(targets, cumulative, points[:, 1])
    return np.column_stack([xs, ys])


def derive_track_outline(
    points: Sequence[tuple[float, float]],
    *,
    target_points: int = 200,
    units_per_metre: float = DEFAULT_UNITS_PER_METRE,
) -> TrackOutline:
    """Turn one lap's worth of ordered (x, y) position samples into a
    `TrackOutline`, resampled evenly by arc length but otherwise left in the
    input's own coordinate units (see the module docstring for why).

    `points` must already be ONE closed lap, in time order (a caller slices
    to a lap window before calling this — this function has no timestamps to
    do that itself). Raises `ValueError` on too few points or a degenerate
    (zero-extent) trace rather than guessing a shape.
    """
    if len(points) < MIN_SOURCE_POINTS:
        raise ValueError(
            f"need at least {MIN_SOURCE_POINTS} position samples to derive a track "
            f"outline, got {len(points)}"
        )
    source_points = len(points)

    deduped = _dedupe(points)
    closed = _close_loop(deduped)
    cumulative = _cumulative_arc_length(closed)
    total_length = cumulative[-1]
    if total_length <= 0:
        raise ValueError("position samples have zero extent; cannot derive a shape")

    resampled = _resample_even(closed, cumulative, target_points)

    xs = [x for x, _ in resampled]
    ys = [y for _, y in resampled]
    width_raw = max(xs) - min(xs)
    height_raw = max(ys) - min(ys)

    return TrackOutline(
        points=resampled,
        width_m=width_raw / units_per_metre,
        height_m=height_raw / units_per_metre,
        source_points=source_points,
    )


def _dedupe(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped = [points[0]]
    for point in points[1:]:
        last = deduped[-1]
        if _distance(last, point) > MIN_POINT_SPACING:
            deduped.append(point)
    return deduped


def _close_loop(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Append the start point if the trace does not already end near where
    it began — a lap slice should already be closed, but this makes the
    guarantee explicit rather than trusting the caller's slicing."""
    if _distance(points[0], points[-1]) > MIN_POINT_SPACING:
        return [*points, points[0]]
    return points


def _cumulative_arc_length(points: list[tuple[float, float]]) -> list[float]:
    cumulative = [0.0]
    for previous, current in pairwise(points):
        cumulative.append(cumulative[-1] + _distance(previous, current))
    return cumulative


def _resample_even(
    points: list[tuple[float, float]], cumulative: list[float], target_points: int
) -> list[tuple[float, float]]:
    """Evenly spaced points by arc length, `target_points` of them, starting
    at the original first point. This is what turns however many raw
    samples arrived (irregular speed, irregular sampling rate) into a
    stable, canvas-friendly point count."""
    total_length = cumulative[-1]
    resampled: list[tuple[float, float]] = []
    segment = 0
    for i in range(target_points):
        target_distance = total_length * i / target_points
        while segment + 2 < len(cumulative) and cumulative[segment + 1] < target_distance:
            segment += 1
        seg_start, seg_end = cumulative[segment], cumulative[segment + 1]
        seg_length = seg_end - seg_start
        fraction = 0.0 if seg_length <= 0 else (target_distance - seg_start) / seg_length
        (x0, y0), (x1, y1) = points[segment], points[segment + 1]
        resampled.append((x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction))
    resampled.append(resampled[0])
    return resampled


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


__all__ = [
    "DEFAULT_UNITS_PER_METRE",
    "MIN_PIT_STOPS",
    "MIN_SOURCE_POINTS",
    "PitLane",
    "TrackOutline",
    "derive_pit_lane",
    "derive_track_outline",
    "derive_track_outline_from_laps",
]
