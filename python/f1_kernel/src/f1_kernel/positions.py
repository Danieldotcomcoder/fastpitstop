"""Measured car positions as one compact track per session.

`CarPosition` is the domain's unit, and every source can still serve those
events one at a time. But a race carries ~640k of them (20 cars at ~4 Hz
for two hours), and a pydantic object per sample is roughly half a
gigabyte, far too much to build per viewer just to draw a map. A
POSITION TRACK is the columnar twin: the same samples with the same
timestamps, as one polars frame (`POSITION_TRACK_SCHEMA`), time-sorted.

Each source builds it the cheapest way it can: `HistoricalSource` reads
the backfill's `car_positions` table, `StoreSource` bulk-reads the
`car_positions` hypertable, and `MemorySource` folds its own events.
`position_frames` then regroups the samples into what the feed actually
sent: one frame per `Position.z` entry, every car at one instant.

ONE SESSION, ONE CLOCK. A position is stamped with the instant the car was
there. A live-ingested session's other events are stamped when OUR
recorder received them (`RawRecord.ts_wall`), a few seconds later: F1
publishes timing after the fact, then the network adds its share. On the
2026 Spanish GP that was a steady ~3.5 s, so positions imported from F1's
archive would show every car crossing the line 3.5 s before the timing
tower counts the lap. `session_clock_offset` measures that gap from the
session's own data (each logged lap against the moment that car actually
passed the line) and `shift_track` moves the positions onto the session's
clock. A backfilled session needs neither: FastF1 already places laps and
positions on one clock (its `t0_date`).

Pure: no I/O in this module; the sources do the loading.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import numpy as np
import polars as pl

from f1_domain import CarPosition, DomainEvent, SessionKey

POSITION_TRACK_SCHEMA: dict[str, pl.DataType] = {
    "ts_utc": pl.Datetime("us", "UTC"),
    "driver": pl.String(),
    "x": pl.Float64(),
    "y": pl.Float64(),
}
"""One row per (car, sample). `x`/`y` are the feed's own track-local units
(~1/10 m), the units every circuit outline is derived in."""

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@runtime_checkable
class PositionTrackSource(Protocol):
    """A source that can hand over a session's positions in bulk."""

    def position_track(self, session_key: SessionKey) -> pl.DataFrame | None:
        """The session's position track, or `None` when it has no measured
        positions at all (the normal case for a live session today: the
        feed withholds them from unauthenticated clients)."""
        ...


@dataclass(frozen=True, slots=True)
class PositionFrame:
    """Every car's position at one instant: one `Position.z` entry."""

    ts_utc: datetime
    cars: tuple[tuple[str, float, float], ...]
    """(driver, x, y), sorted by driver."""


def normalize_track(frame: pl.DataFrame) -> pl.DataFrame | None:
    """Coerce `frame` (columns `ts_utc`, `driver`, `x`, `y`) to the track
    schema: tz-aware UTC timestamps (a naive one is read as UTC, as every
    backfill timestamp is), no missing coordinates, sorted by time then
    driver. `None` when nothing is left."""
    ts = frame["ts_utc"]
    dtype = ts.dtype
    if isinstance(dtype, pl.Datetime) and dtype.time_zone is None:
        ts = ts.dt.replace_time_zone("UTC")
    track = (
        frame.with_columns(ts.alias("ts_utc"))
        .select(
            pl.col("ts_utc").cast(POSITION_TRACK_SCHEMA["ts_utc"]),
            pl.col("driver").cast(pl.String),
            pl.col("x").cast(pl.Float64),
            pl.col("y").cast(pl.Float64),
        )
        .drop_nulls()
        .sort(["ts_utc", "driver"], maintain_order=True)
    )
    return None if track.is_empty() else track


def track_from_events(events: Iterable[DomainEvent]) -> pl.DataFrame | None:
    """The track for whatever `CarPosition` events `events` holds, ignoring
    every other kind. For a source that has only events."""
    rows = [
        (event.ts_utc, event.driver, event.x, event.y)
        for event in events
        if isinstance(event, CarPosition)
        and event.driver is not None
        and event.x is not None
        and event.y is not None
    ]
    if not rows:
        return None
    frame = pl.DataFrame(
        rows, schema=["ts_utc", "driver", "x", "y"], orient="row", infer_schema_length=None
    )
    return normalize_track(frame)


MIN_CLOCK_MATCHES = 20
"""Laps that must be matched to a line crossing before an offset is
trusted. A race gives hundreds; fewer than this is a session too thin (or a
circuit outline too wrong) to measure."""

_CLOCK_SEARCH_S = 15.0
"""How far either side of a logged lap to look for that car passing the
line. Feed delay is seconds; a lap is a minute or more, so one pass only."""

_LINE_REACH = 400.0
"""How close (feed units, ~40 m) a sample must come to the line point to
count as passing it. Excludes a car the feed had not placed near the line
(a missing stretch of samples) rather than letting it skew the median."""


def session_clock_offset(
    track: pl.DataFrame,
    laps: Iterable[tuple[str, datetime]],
    line: tuple[float, float],
) -> timedelta | None:
    """How far the session's own clock runs behind the positions' clock.

    `laps` is each logged line crossing, `(driver, when the session logged
    it)`; `line` is the start/finish point in track units (a circuit
    outline's first point). For each lap, the car's sample nearest the line
    within `_CLOCK_SEARCH_S` is when it really crossed; the median of
    (logged - crossed) over every matched lap is the offset. `None` when
    fewer than `MIN_CLOCK_MATCHES` laps match."""
    per_driver: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for (driver,), group in track.group_by("driver"):
        epoch_us = group["ts_utc"].dt.epoch("us").to_numpy()
        reach = np.hypot(group["x"].to_numpy() - line[0], group["y"].to_numpy() - line[1])
        order = np.argsort(epoch_us, kind="stable")
        per_driver[str(driver)] = (epoch_us[order], reach[order])

    window_us = int(_CLOCK_SEARCH_S * 1_000_000)
    offsets_us: list[int] = []
    for driver, logged in laps:
        samples = per_driver.get(driver)
        if samples is None:
            continue
        epoch_us, reach = samples
        logged_us = (logged - _EPOCH) // timedelta(microseconds=1)
        lo = int(np.searchsorted(epoch_us, logged_us - window_us, side="left"))
        hi = int(np.searchsorted(epoch_us, logged_us + window_us, side="right"))
        if hi <= lo:
            continue
        nearest = lo + int(np.argmin(reach[lo:hi]))
        if reach[nearest] > _LINE_REACH:
            continue
        offsets_us.append(logged_us - int(epoch_us[nearest]))
    if len(offsets_us) < MIN_CLOCK_MATCHES:
        return None
    return timedelta(microseconds=float(np.median(offsets_us)))


OFF_CIRCUIT_UNITS = 1500.0
"""Samples further than about this (feed units, ~150 m) from both the racing
line and the pit lane are not a place on the circuit. The feed has more
than one placeholder for "no fix": (0, 0, 0) in the garage, which the
backfill drops, and at least one real coordinate every car reports at
once (2025 Jeddah FP1: all twenty cars 1.5 km from the track for twenty
minutes). Drawn, those send a car across the map and back."""


def near_lines(
    track: pl.DataFrame,
    lines: Sequence[Sequence[tuple[float, float]]],
    max_distance: float = OFF_CIRCUIT_UNITS,
) -> pl.DataFrame:
    """The samples within about `max_distance` of any of `lines`.

    A grid test, not an exact distance: the lines are rasterised into
    cells half `max_distance` wide, dilated by two cells, and each sample is
    kept when its cell is marked. Everything within `max_distance` is kept;
    nothing further than about 1.8 `max_distance` is. Linear in the number
    of samples, so a whole race filters in milliseconds."""
    if track.is_empty() or not lines:
        return track
    cell = max_distance / 2.0
    marked: set[tuple[int, int]] = set()
    for line in lines:
        points = np.asarray(line, dtype=float).reshape(-1, 2)
        if len(points) == 0:
            continue
        steps: np.ndarray = np.hypot(*np.diff(points, axis=0).T)
        along: np.ndarray = np.concatenate([[0.0], np.cumsum(steps)])
        at: np.ndarray = np.linspace(0.0, float(along[-1]), int(along[-1] / (cell / 4)) + 2)
        xs: np.ndarray = np.floor(np.interp(at, along, points[:, 0]) / cell)
        ys: np.ndarray = np.floor(np.interp(at, along, points[:, 1]) / cell)
        marked.update(zip(xs.astype(np.int64).tolist(), ys.astype(np.int64).tolist(), strict=True))
    reach = 2
    dilated = {
        (cx + dx, cy + dy)
        for cx, cy in marked
        for dx in range(-reach, reach + 1)
        for dy in range(-reach, reach + 1)
    }
    keys = np.fromiter((cx * 1_000_003 + cy for cx, cy in dilated), dtype=np.int64)
    cx = np.floor(track["x"].to_numpy() / cell).astype(np.int64)
    cy = np.floor(track["y"].to_numpy() / cell).astype(np.int64)
    keep = np.isin(cx * 1_000_003 + cy, keys)
    return track.filter(pl.Series(keep))


def distance_to_lines(
    xs: np.ndarray, ys: np.ndarray, lines: Sequence[Sequence[tuple[float, float]]]
) -> np.ndarray:
    """Each point's distance to the nearest segment of any of `lines`,
    in the same units. Chunked, so a few thousand points against a few
    hundred segments stays small in memory."""
    best = np.full(len(xs), np.inf)
    for line in lines:
        points = np.asarray(line, dtype=float).reshape(-1, 2)
        if len(points) < 2:
            continue
        ax, ay = points[:-1, 0], points[:-1, 1]
        vx, vy = points[1:, 0] - ax, points[1:, 1] - ay
        seg2 = np.maximum(vx * vx + vy * vy, 1e-9)
        for start in range(0, len(xs), 2048):
            x = xs[start : start + 2048, None]
            y = ys[start : start + 2048, None]
            t = np.clip(((x - ax) * vx + (y - ay) * vy) / seg2, 0.0, 1.0)
            d = np.hypot(x - (ax + t * vx), y - (ay + t * vy)).min(axis=1)
            best[start : start + 2048] = np.minimum(best[start : start + 2048], d)
    return best


def window_traces(
    track: pl.DataFrame, windows: Iterable[tuple[str, datetime, datetime]]
) -> list[list[tuple[float, float]]]:
    """For each `(driver, start, end)`, that driver's samples in the
    window, in time order: one lap, or one trip down the pit lane."""
    by_driver = {str(driver): group for (driver,), group in track.group_by("driver")}
    traces: list[list[tuple[float, float]]] = []
    for driver, start, end in windows:
        group = by_driver.get(driver)
        if group is None:
            continue
        inside = group.filter((pl.col("ts_utc") >= start) & (pl.col("ts_utc") <= end)).sort(
            "ts_utc"
        )
        traces.append(list(zip(inside["x"].to_list(), inside["y"].to_list(), strict=True)))
    return traces


def shift_track(track: pl.DataFrame, offset: timedelta) -> pl.DataFrame:
    """Every sample moved `offset` later: the track on another clock."""
    return track.with_columns(pl.col("ts_utc") + offset)


def position_frames(track: pl.DataFrame) -> Iterator[PositionFrame]:
    """Regroup a track into frames, one per distinct timestamp, in order.

    Lazy on purpose: the replay stream consumes frames at the race's own
    pace, so only the arrays below stay resident, never 640k Python
    tuples at once."""
    if track.is_empty():
        return
    epoch_us = track["ts_utc"].dt.epoch("us").to_numpy()
    names = track["driver"].unique().sort().to_list()
    codes = (track["driver"].rank("dense").to_numpy() - 1).astype(np.int64)
    xs = track["x"].to_numpy()
    ys = track["y"].to_numpy()
    bounds = (np.flatnonzero(np.diff(epoch_us)) + 1).tolist()
    starts = [0, *bounds]
    ends = [*bounds, len(epoch_us)]
    for start, end in zip(starts, ends, strict=True):
        yield PositionFrame(
            ts_utc=_EPOCH + timedelta(microseconds=int(epoch_us[start])),
            cars=tuple(
                (names[int(codes[i])], float(xs[i]), float(ys[i])) for i in range(start, end)
            ),
        )


__all__ = [
    "OFF_CIRCUIT_UNITS",
    "POSITION_TRACK_SCHEMA",
    "PositionFrame",
    "PositionTrackSource",
    "distance_to_lines",
    "near_lines",
    "normalize_track",
    "position_frames",
    "session_clock_offset",
    "shift_track",
    "track_from_events",
    "window_traces",
]
