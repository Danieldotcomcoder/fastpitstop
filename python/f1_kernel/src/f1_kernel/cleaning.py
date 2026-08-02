"""The clean-lap filter: which laps are evidence, and which are noise.

This is the single biggest reason our numbers should be trustworthy where
public tools' are not. A degradation slope fitted over a safety-car lap, an
in-lap and two laps stuck behind a backmarker is not a degradation slope,
it is an average of unrelated things. Everything downstream in Phase 2
consumes `clean_laps`; the UI consumes `annotate_laps`, because "excluded"
is only useful to a user if it comes with *why*.

Nothing is ever silently dropped. `annotate_laps` returns EVERY row with a
boolean per reason plus `exclusion_reasons`, and `clean_laps` is exactly
that filtered to `is_clean`.

## The exclusions

- `is_lap1` — a standing start is not a representative lap.
- `is_in_lap` / `is_out_lap` — the driver is braking for the pit entry or
  leaving on cold tyres; neither is a lap the car could have run.
- `is_sc` / `is_vsc` / `is_yellow` / `is_red` — the lap overlapped a
  non-green track status, wholly or partly. Taken from
  `track_status_flags`, which lists EVERY status the lap window touched, so
  a lap that went yellow then safety car is flagged as both.
- `is_dirty_air` — running in another car's wake. **Definition**: the
  MEDIAN of the car-ahead interval samples inside the lap window is below
  `params.dirty_air_threshold_s`. Using the median means the flag fires
  when the driver was within the threshold for at least half the lap, which
  is what "a meaningful portion" has to mean if it is to be reproducible;
  one lunge into a gap does not condemn a lap, and a lap spent glued to a
  gearbox is not rescued by one clear moment. It needs
  `params.dirty_air_min_samples` samples to be considered known — see
  "unknown is not clean-by-default" below.
- `is_anomalous` — the lap time is outside a robust bound within its stint:
  `|lap_time - median| > max(3.5 * 1.4826 * MAD, outlier_floor_s)`, with
  the multiplier and floor from params. Median and MAD, never mean and
  stddev: one 12-second lock-up moves a mean and a stddev enough to hide
  itself, and moves a median and a MAD hardly at all.

## Two decisions worth knowing

**The anomaly bound is estimated from candidate laps only** — laps that
already survived every other filter. Otherwise safety-car laps (20s slow)
would inflate the spread until nothing looked anomalous, and the filter
would quietly stop working on exactly the races that need it. For the same
reason a lap already excluded for another reason is not additionally
flagged anomalous.

**Unknown is not the same as excluded.** If a session has no gap channel,
every lap's dirty-air state is unknown; excluding them all would leave
nothing, and including them silently would overstate confidence. So an
unknown reason does not exclude, and `dirty_air_known` / `anomaly_known`
report it. That is the quality signal CLAUDE.md requires — a caller
aggregating over laps can weight or refuse accordingly.
"""

from __future__ import annotations

import polars as pl

from f1_domain import TrackStatusKind
from f1_kernel.params import KernelParams
from f1_kernel.schemas import LAP_FRAME_SCHEMA, Schema, empty_frame

MAD_TO_SIGMA = 1.4826
"""Scales a median-absolute-deviation onto the same footing as a standard
deviation for normally distributed data, so `outlier_mad_multiplier` reads
like a sigma multiple without inheriting a mean's fragility."""

REASON_COLUMNS: tuple[str, ...] = (
    "is_lap1",
    "is_in_lap",
    "is_out_lap",
    "is_sc",
    "is_vsc",
    "is_yellow",
    "is_red",
    "is_dirty_air",
    "is_anomalous",
)
"""Every reason a lap can be excluded. `is_clean` is the negation of all of
them; `exclusion_reasons` lists the ones that fired, in this order."""

QUALITY_COLUMNS: tuple[str, ...] = ("dirty_air_known", "anomaly_known")
"""Whether each judgement could actually be made for this lap."""

ANNOTATION_SCHEMA: Schema = {
    **{name: pl.Boolean() for name in REASON_COLUMNS},
    **{name: pl.Boolean() for name in QUALITY_COLUMNS},
    "is_clean": pl.Boolean(),
    "exclusion_reasons": pl.List(pl.String()),
}

ANNOTATED_LAP_FRAME_SCHEMA: Schema = {**LAP_FRAME_SCHEMA, **ANNOTATION_SCHEMA}
"""The lap frame plus every annotation — what `annotate_laps` returns."""

_REQUIRED_COLUMNS = (
    "driver",
    "lap_number",
    "lap_time_s",
    "stint_number",
    "track_status_flags",
    "gap_ahead_s",
    "gap_ahead_samples",
    "is_pit_in",
    "is_pit_out",
)


def annotate_laps(laps: pl.DataFrame, params: KernelParams) -> pl.DataFrame:
    """Every lap, with a boolean per exclusion reason, `is_clean`, the
    quality signals, and a human-readable `exclusion_reasons` list.

    Pure and deterministic: same frame and params in, identical frame out.
    Row order is preserved, so an annotated frame lines up with the frame it
    came from.
    """
    _require_columns(laps)
    if laps.is_empty():
        return empty_frame(ANNOTATED_LAP_FRAME_SCHEMA)

    annotated = laps.with_columns(_context_reasons(params))
    annotated = annotated.with_columns(_anomaly_columns(params))
    annotated = annotated.with_columns(
        pl.any_horizontal([pl.col(name) for name in REASON_COLUMNS]).not_().alias("is_clean"),
        _exclusion_reasons(),
    )
    return annotated.select(
        [*laps.columns, *REASON_COLUMNS, *QUALITY_COLUMNS, "is_clean", "exclusion_reasons"]
    )


def clean_laps(laps: pl.DataFrame, params: KernelParams) -> pl.DataFrame:
    """Only the laps that survived every filter — what every downstream
    analytic fits on. Same columns as `annotate_laps` (the reasons ride
    along, all False), so a caller can always show its working."""
    return annotate_laps(laps, params).filter(pl.col("is_clean"))


def clean_lap_summary(laps: pl.DataFrame, params: KernelParams) -> pl.DataFrame:
    """Per-driver counts: laps, clean laps, and how many fell to each
    reason. The at-a-glance sanity check on a race — and the shape a UI
    needs to explain "why is this driver's degradation estimate weak?"."""
    annotated = annotate_laps(laps, params)
    if annotated.is_empty():
        return pl.DataFrame(
            schema={
                "driver": pl.String(),
                "laps": pl.Int32(),
                "clean_laps": pl.Int32(),
                "clean_fraction": pl.Float64(),
                **{name: pl.Int32() for name in REASON_COLUMNS},
            }
        )
    return (
        annotated.group_by("driver")
        .agg(
            pl.len().cast(pl.Int32).alias("laps"),
            pl.col("is_clean").sum().cast(pl.Int32).alias("clean_laps"),
            *[pl.col(name).sum().cast(pl.Int32).alias(name) for name in REASON_COLUMNS],
        )
        .with_columns(
            (pl.col("clean_laps") / pl.col("laps")).alias("clean_fraction"),
        )
        .select("driver", "laps", "clean_laps", "clean_fraction", *REASON_COLUMNS)
        .sort("driver")
    )


def _context_reasons(params: KernelParams) -> list[pl.Expr]:
    """Everything decidable from the lap row alone."""
    flags = pl.col("track_status_flags")
    dirty_air_known = (pl.col("gap_ahead_samples") >= params.dirty_air_min_samples) & pl.col(
        "gap_ahead_s"
    ).is_not_null()
    return [
        (pl.col("lap_number") == 1).fill_null(value=False).alias("is_lap1"),
        pl.col("is_pit_in").fill_null(value=False).alias("is_in_lap"),
        pl.col("is_pit_out").fill_null(value=False).alias("is_out_lap"),
        _has_status(flags, TrackStatusKind.SC).alias("is_sc"),
        _has_status(flags, TrackStatusKind.VSC).alias("is_vsc"),
        _has_status(flags, TrackStatusKind.YELLOW).alias("is_yellow"),
        _has_status(flags, TrackStatusKind.RED).alias("is_red"),
        dirty_air_known.alias("dirty_air_known"),
        (dirty_air_known & (pl.col("gap_ahead_s") < params.dirty_air_threshold_s))
        .fill_null(value=False)
        .alias("is_dirty_air"),
    ]


def _has_status(flags: pl.Expr, status: TrackStatusKind) -> pl.Expr:
    """Whether the lap window touched `status` at any point."""
    return flags.list.contains(status.value).fill_null(value=False)


def _anomaly_columns(params: KernelParams) -> list[pl.Expr]:
    """The robust within-stint outlier bound.

    Estimated over CANDIDATE laps only (those that passed every other
    filter) using window functions over `(driver, stint_number)`. A null
    stint number forms its own group, which means a source that emits no
    stint events falls back to one group per driver — documented in
    `schemas.LAP_FRAME_DOCS`.
    """
    group = ["driver", "stint_number"]
    candidate = (
        pl.col("lap_time_s").is_not_null()
        & pl.any_horizontal(
            [pl.col(name) for name in REASON_COLUMNS if name != "is_anomalous"]
        ).not_()
    )
    candidate_time = pl.when(candidate).then(pl.col("lap_time_s")).otherwise(None)

    median = candidate_time.median().over(group)
    candidate_count = candidate_time.count().over(group)
    deviation = (pl.col("lap_time_s") - median).abs()
    mad = pl.when(candidate).then(deviation).otherwise(None).median().over(group)

    bound = pl.max_horizontal(
        mad * MAD_TO_SIGMA * params.outlier_mad_multiplier,
        pl.lit(params.outlier_floor_s),
    )
    known = candidate_count >= params.outlier_min_laps
    return [
        known.fill_null(value=False).alias("anomaly_known"),
        (candidate & known & (deviation > bound)).fill_null(value=False).alias("is_anomalous"),
    ]


def _exclusion_reasons() -> pl.Expr:
    """The reasons that fired, as a list of column names — what a UI shows
    instead of a lap silently missing from a chart."""
    return (
        pl.concat_list(
            [pl.when(pl.col(name)).then(pl.lit(name)).otherwise(None) for name in REASON_COLUMNS]
        )
        .list.drop_nulls()
        .alias("exclusion_reasons")
    )


def _require_columns(laps: pl.DataFrame) -> None:
    missing = [column for column in _REQUIRED_COLUMNS if column not in laps.columns]
    if missing:
        raise ValueError(
            f"lap frame is missing required column(s) {missing}; "
            "build it with f1_kernel.lap_frame so the schema is guaranteed"
        )
