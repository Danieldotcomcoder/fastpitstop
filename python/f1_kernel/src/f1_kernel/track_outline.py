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


__all__ = ["DEFAULT_UNITS_PER_METRE", "MIN_SOURCE_POINTS", "TrackOutline", "derive_track_outline"]
