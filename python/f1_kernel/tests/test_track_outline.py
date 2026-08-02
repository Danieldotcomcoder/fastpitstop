"""`derive_track_outline`: a pure transform over (x, y) points already in
hand, tested with synthetic point clouds standing in for a real position
trace — the same discipline every other Phase 2 derivation (fuel, pit loss,
DRS zones) uses for its own pure core, since a real trace needs a real
FastF1 session/recording this repo does not ship."""

from __future__ import annotations

import math

import pytest

from f1_kernel.track_outline import MIN_SOURCE_POINTS, TrackOutline, derive_track_outline


def _rectangle(width: float, height: float, n_per_side: int) -> list[tuple[float, float]]:
    """A closed rectangular "lap" — an easy shape to assert an aspect ratio
    and a known total perimeter against."""
    half_w, half_h = width / 2, height / 2
    corners = [
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
    ]
    points: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1], strict=True):
        for i in range(n_per_side):
            t = i / n_per_side
            points.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return points


def _circle(radius: float, n: int) -> list[tuple[float, float]]:
    return [
        (radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def test_too_few_points_raises() -> None:
    with pytest.raises(ValueError, match="at least"):
        derive_track_outline([(0.0, 0.0)] * (MIN_SOURCE_POINTS - 1))


def test_degenerate_trace_raises() -> None:
    """Every sample at the same point (a parked car) has no shape to give."""
    with pytest.raises(ValueError, match="zero extent"):
        derive_track_outline([(5.0, 5.0)] * 20)


def test_output_is_closed() -> None:
    outline = derive_track_outline(_circle(100.0, 60), target_points=50)

    assert outline.points[0] == outline.points[-1]
    assert len(outline.points) == 51


def test_points_stay_in_the_inputs_own_coordinate_units_untransformed() -> None:
    """`points` is NOT centered or rescaled to a canonical display range —
    a live `car_position` sample (same raw units, same session) must be
    plottable directly alongside these points with no extra transform. The
    resampled bounding box must therefore match the INPUT's own bounding box
    (mod the small approximation resampling/dedup introduces), not some
    normalized [-1, 1] range."""
    raw = _rectangle(width=2000.0, height=1000.0, n_per_side=40)
    outline = derive_track_outline(raw)

    input_xs = [x for x, _ in raw]
    input_ys = [y for _, y in raw]
    output_xs = [x for x, _ in outline.points]
    output_ys = [y for _, y in outline.points]

    assert min(output_xs) == pytest.approx(min(input_xs), abs=1.0)
    assert max(output_xs) == pytest.approx(max(input_xs), abs=1.0)
    assert min(output_ys) == pytest.approx(min(input_ys), abs=1.0)
    assert max(output_ys) == pytest.approx(max(input_ys), abs=1.0)


def test_aspect_ratio_is_preserved_not_squashed_to_a_square() -> None:
    """A 2:1 rectangle's bounding box must stay ~2:1 — nothing here distorts
    the real shape's proportions."""
    outline = derive_track_outline(_rectangle(width=2000.0, height=1000.0, n_per_side=40))

    xs = [x for x, _ in outline.points]
    ys = [y for _, y in outline.points]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)

    assert width / height == pytest.approx(2.0, rel=0.05)
    assert outline.width_m == pytest.approx(200.0, rel=0.05)
    assert outline.height_m == pytest.approx(100.0, rel=0.05)


def test_source_points_reports_the_raw_count_before_resampling() -> None:
    raw = _circle(100.0, 37)
    outline = derive_track_outline(raw, target_points=200)

    assert outline.source_points == 37
    assert len(outline.points) == 201


def test_resampling_is_stable_regardless_of_raw_sample_count() -> None:
    """Sparse and dense traces of the same real shape converge on nearly the
    same normalized outline — this is what "degrade gracefully on sparse
    position data" means for the shape itself."""
    dense = derive_track_outline(_circle(100.0, 400), target_points=64)
    sparse = derive_track_outline(_circle(100.0, 24), target_points=64)

    # Tolerance is a few percent of the circle's own radius (100) — enough to
    # cover the sparse 24-gon's real polygon-approximation error (chord
    # sagitta ~0.86 at this radius) without masking an actual regression.
    for (dx, dy), (sx, sy) in zip(dense.points, sparse.points, strict=True):
        assert dx == pytest.approx(sx, abs=5.0)
        assert dy == pytest.approx(sy, abs=5.0)


def test_duplicate_consecutive_points_do_not_break_resampling() -> None:
    """A car sitting still (formation lap, pit lane) sends repeated
    identical samples; they must not create zero-length segments that break
    arc-length interpolation."""
    points = _circle(100.0, 60)
    stalled = [*points[:10], points[9], points[9], points[9], *points[10:]]

    outline = derive_track_outline(stalled, target_points=50)

    assert isinstance(outline, TrackOutline)
    assert len(outline.points) == 51


def test_already_closed_input_is_not_double_closed() -> None:
    points = _circle(100.0, 60)
    already_closed = [*points, points[0]]

    outline = derive_track_outline(already_closed, target_points=50)

    assert len(outline.points) == 51
