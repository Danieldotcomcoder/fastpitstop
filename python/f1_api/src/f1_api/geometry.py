"""The track map's geometry for one session, and the positions drawn on it.

The map draws two lines, the racing line and the pit lane, and dots that
must sit on them. The circuit files derive both lines from one reference
race (mostly 2024). F1's coordinate frame is usually the same from season
to season, but not always: 2025 Suzuka sits a median 13 m (up to 30 m)
off its 2024 outline, and no rotation, scale or shift reconciles the two,
so its cars drove through the grass. `session_positions` therefore checks
the circuit file's lines against the session's own measured positions
and, when they do not fit, derives both lines from the session itself:
the racing line from its fastest clean laps, the pit lane from its own
pit stops. Dots and lines then share one frame by construction. When they
do fit (every other 2025 session), the circuit file's lines are used.

The WebSocket and `GET /sessions/{key}/circuit` both come through here,
so the lines a viewer sees and the dots drawn on them can never come from
different geometry.

It also decides which measured positions exist for a session at all
(`_measured_track`): the source's own, else F1's post-session archive,
put on the session's own clock (`f1_kernel.positions`, "one session, one
clock"), minus anything nowhere near the circuit (`near_lines`).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

import numpy as np
import polars as pl

from f1_api.config import Settings
from f1_api.stream.replay import source_position_track
from f1_domain import LapCompleted, PitEntry, PitExit, SessionKey
from f1_kernel import (
    Circuit,
    EventSource,
    HistoricalSource,
    PitLane,
    TrackOutline,
    derive_pit_lane,
    derive_track_outline_from_laps,
    distance_to_lines,
    near_lines,
    session_clock_offset,
    shift_track,
    window_traces,
)

_log = logging.getLogger(__name__)

FIT_TOLERANCE_UNITS = 80.0
"""How far (feed units, ~8 m) the 90th-percentile sample may sit from the
circuit file's lines before those lines are taken not to describe the
session. Every 2025 session sharing its circuit's frame is at or under
4 m; every 2025 Suzuka session is at 27 m or more. The 90th percentile,
not the median: two Suzuka sessions had a median under 3 m (much of the
lap coincides) while a quarter of their samples sat 15-30 m off."""

FIT_PERCENTILE = 90.0

REFERENCE_LAPS = 7
"""How many of the session's fastest clean laps a derived racing line is
the median of."""

MIN_REFERENCE_LAPS = 3

LAP_SLACK = 1.07
"""A lap slower than this times the session's fastest is not racing pace
(a cool-down, a safety car): its line is not the racing line."""

LENGTH_SLACK = 0.15
"""A derived racing line must measure within this fraction of the circuit's
known lap distance, or it is not a lap of this circuit."""

MAX_PIT_TRAVERSE = timedelta(minutes=30)
"""How long a pit visit (entry to exit) may last and still trace the pit
lane. Long on purpose: in practice most visits end in the garage for
minutes (2025 Suzuka FP2, red-flagged: 56 visits, median 7 minutes, one
under two). The drive in and out still runs down the lane, and each team's
detour into its own garage averages out across visits the way a box
swerve does."""

_FIT_SAMPLES = 6000
_CACHE_SIZE = 32


@dataclass(frozen=True, slots=True)
class SessionGeometry:
    outline: TrackOutline | None
    pit_lane: PitLane | None
    from_session: bool = False
    """True when both lines were derived from this session's own positions,
    because the circuit file's did not fit them."""


_cache: OrderedDict[tuple[SessionKey, int], SessionGeometry] = OrderedDict()
_cache_lock = threading.Lock()


def session_positions(
    settings: Settings,
    source: EventSource,
    session_key: SessionKey,
    circuit: Circuit | None,
) -> tuple[SessionGeometry, pl.DataFrame | None]:
    """The lines to draw and the measured positions to draw on them.

    Never raises: a session whose positions or geometry cannot be worked
    out gets the circuit file's lines and no positions, and the map falls
    back to its estimate, which beats no map."""
    shipped = SessionGeometry(
        circuit.outline if circuit else None, circuit.pit_lane if circuit else None
    )
    try:
        track = _measured_track(settings, source, session_key, circuit)
        if track is None or circuit is None:
            return shipped, track
        geometry = _geometry(track, source, session_key, circuit)
        lines = _lines(geometry)
        if not lines:
            return geometry, track
        kept = near_lines(track, lines)
        return geometry, None if kept.is_empty() else kept
    except Exception:
        _log.warning("could not work out positions for %s", session_key, exc_info=True)
        return shipped, None


def _lines(geometry: SessionGeometry) -> list[list[tuple[float, float]]]:
    lines: list[list[tuple[float, float]]] = []
    if geometry.outline is not None:
        lines.append(geometry.outline.points)
    if geometry.pit_lane is not None:
        lines.append(geometry.pit_lane.points)
    return lines


def _fit_distance(track: pl.DataFrame, lines: list[list[tuple[float, float]]]) -> float:
    """The `FIT_PERCENTILE`th-percentile distance from the session's
    samples to `lines`: how well the lines describe where the cars were."""
    step = max(1, len(track) // _FIT_SAMPLES)
    sample = track.gather_every(step)
    distances = distance_to_lines(sample["x"].to_numpy(), sample["y"].to_numpy(), lines)
    return float(np.percentile(distances, FIT_PERCENTILE))


def _geometry(
    track: pl.DataFrame, source: EventSource, session_key: SessionKey, circuit: Circuit
) -> SessionGeometry:
    key = (session_key, len(track))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    shipped = SessionGeometry(circuit.outline, circuit.pit_lane)
    lines = _lines(shipped)
    geometry = shipped
    if not lines or _fit_distance(track, lines) > FIT_TOLERANCE_UNITS:
        derived = _derive(track, source, session_key, circuit)
        if derived is not None:
            geometry = derived
            _log.info("track geometry for %s derived from its own positions", session_key)
    with _cache_lock:
        _cache[key] = geometry
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return geometry


def _derive(
    track: pl.DataFrame, source: EventSource, session_key: SessionKey, circuit: Circuit
) -> SessionGeometry | None:
    """Both lines from the session's own laps and stops, or `None` when the
    session cannot give a trustworthy racing line."""
    laps: dict[str, list[LapCompleted]] = {}
    pit_times: dict[str, list[tuple[datetime, str]]] = {}
    for event in source.events(session_key, kinds=["lap_completed", "pit_entry", "pit_exit"]):
        if isinstance(event, LapCompleted) and event.driver is not None:
            laps.setdefault(event.driver, []).append(event)
        elif isinstance(event, PitEntry | PitExit) and event.driver is not None:
            pit_times.setdefault(event.driver, []).append((event.ts_utc, event.kind))

    candidates: list[tuple[float, str, datetime, datetime]] = []
    for driver, driver_laps in laps.items():
        ordered = sorted(
            (lap for lap in driver_laps if lap.lap_number is not None),
            key=lambda lap: lap.lap_number or 0,
        )
        stops = [ts for ts, _ in pit_times.get(driver, [])]
        for previous, lap in pairwise(ordered):
            if lap.lap_number != (previous.lap_number or 0) + 1 or lap.lap_time is None:
                continue
            start, end = previous.ts_utc, lap.ts_utc
            if abs((end - start) - lap.lap_time) > timedelta(seconds=2):
                continue  # the timestamps do not bound this lap
            if any(
                start - timedelta(seconds=5) <= ts <= end + timedelta(seconds=5) for ts in stops
            ):
                continue  # an in- or out-lap drives the pit lane, not the racing line
            candidates.append((lap.lap_time.total_seconds(), driver, start, end))
    if len(candidates) < MIN_REFERENCE_LAPS:
        return None
    candidates.sort()
    fastest = candidates[0][0]
    chosen = [c for c in candidates if c[0] <= fastest * LAP_SLACK][:REFERENCE_LAPS]
    if len(chosen) < MIN_REFERENCE_LAPS:
        return None
    try:
        outline = derive_track_outline_from_laps(
            window_traces(track, [(driver, start, end) for _, driver, start, end in chosen])
        )
    except ValueError:
        return None
    length_m = float(np.hypot(*np.diff(np.asarray(outline.points), axis=0).T).sum()) / 10.0
    if abs(length_m - circuit.lap_distance_m) > LENGTH_SLACK * circuit.lap_distance_m:
        return None

    pit_lane: PitLane | None = None
    windows: list[tuple[str, datetime, datetime]] = []
    for driver, times in pit_times.items():
        ordered_times = sorted(times)
        for (entered, kind), (left, next_kind) in pairwise(ordered_times):
            is_stop = kind == "pit_entry" and next_kind == "pit_exit"
            if is_stop and entered < left <= entered + MAX_PIT_TRAVERSE:
                windows.append((driver, entered, left))
    try:
        pit_lane = derive_pit_lane(window_traces(track, windows))
    except ValueError:
        pit_lane = None

    derived = SessionGeometry(outline, pit_lane, from_session=True)
    if _fit_distance(track, _lines(derived)) > FIT_TOLERANCE_UNITS:
        return None
    return derived


def _measured_track(
    settings: Settings,
    source: EventSource,
    session_key: SessionKey,
    circuit: Circuit | None,
) -> pl.DataFrame | None:
    """Measured positions for a session, before any geometry: the source's
    own first, else the post-session archive under the historical root.

    The archive is how a live-ingested session gets positions at all. The
    live feed withholds them from our (unauthenticated) client, but F1
    publishes them once the session is over, and `f1-backfill
    --positions-only --uncatalogued` (or the `positions` service) files
    them by session key right where a backfilled session keeps its own.
    Outside a backfilled session the track is then put on the session's own
    clock: its timing was stamped on receipt, seconds after the cars' own
    position timestamps. That needs the circuit's start/finish point;
    without an outline the track is used as it is."""
    if isinstance(source, HistoricalSource):
        return source.position_track(session_key)
    track = source_position_track(source, session_key)
    if track is None:
        track = HistoricalSource(settings.historical_root).position_track(session_key)
    if track is None or circuit is None or circuit.outline is None:
        return track
    laps = [
        (driver, event.ts_utc)
        for event in source.events(session_key, kinds=["lap_completed"])
        if isinstance(driver := getattr(event, "driver", None), str)
    ]
    offset = session_clock_offset(track, laps, circuit.outline.points[0])
    if offset is None:
        _log.warning("could not align car positions to %s's clock", session_key)
        return track
    _log.info("car positions for %s aligned by %.2fs", session_key, offset.total_seconds())
    return shift_track(track, offset)


__all__ = [
    "FIT_TOLERANCE_UNITS",
    "SessionGeometry",
    "session_positions",
]
