"""The STABLE frame schemas — Phase 2's contract with everything above it.

`f1_domain.DomainEvent` was Phase 1's contract; these three schemas are
Phase 2's. Every analytic, every backtest and (later) every UI query reads
columns named here, so treat them like a database schema:

- **Adding** a column is safe and normal.
- **Renaming, retyping or removing** one breaks every analytic written
  against it. Do it deliberately, in one commit, with the analytics updated.
- Column ORDER is part of the schema, so a frame is comparable and
  snapshot-testable without reordering games.

Units are in the names (`_s` seconds, `_m` metres, `_kph`, `_pct`), which is
the one convention that stops a fuel-correction bug being invisible.
Everything nullable is genuinely nullable: the feed drops fields, history
reconstructs them, and a lap with no gap data must be distinguishable from a
lap with a zero gap.
"""

from __future__ import annotations

import polars as pl

Schema = dict[str, pl.DataType]

_UTC = pl.Datetime("us", "UTC")

LAP_FRAME_SCHEMA: Schema = {
    # -- identity -------------------------------------------------------
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "lap_number": pl.Int32(),
    # -- timing ---------------------------------------------------------
    "lap_start_utc": _UTC,
    "lap_end_utc": _UTC,
    "lap_time_s": pl.Float64(),
    "sector1_s": pl.Float64(),
    "sector2_s": pl.Float64(),
    "sector3_s": pl.Float64(),
    "is_personal_best": pl.Boolean(),
    # -- tyre / stint ---------------------------------------------------
    "compound": pl.String(),
    "tyre_age": pl.Int32(),
    "stint_number": pl.Int32(),
    # -- race context ---------------------------------------------------
    "position": pl.Int32(),
    "gap_ahead_s": pl.Float64(),
    "gap_ahead_min_s": pl.Float64(),
    "gap_ahead_samples": pl.Int32(),
    "gap_to_leader_s": pl.Float64(),
    "track_status": pl.String(),
    "track_status_flags": pl.List(pl.String()),
    "is_pit_in": pl.Boolean(),
    "is_pit_out": pl.Boolean(),
    # -- provenance -----------------------------------------------------
    "provenance": pl.String(),
}
"""One row per lap a driver was observed completing, sorted by
`(driver, lap_number)`."""

LAP_FRAME_DOCS: dict[str, str] = {
    "driver": "Racing number as a string ('44'), the feed's own primary key.",
    "lap_number": "1-based lap number as the feed/FastF1 counts it.",
    "lap_start_utc": (
        "Start of the lap window: `lap_end_utc - lap_time_s`, or the previous "
        "lap's end when this lap has no time. Everything window-scoped "
        "(gaps, track status) is measured over `(lap_start_utc, lap_end_utc]`."
    ),
    "lap_end_utc": "When the driver crossed the line to complete this lap.",
    "lap_time_s": (
        "Lap time in seconds. NULL is normal and meaningful: the feed drops it "
        "on some pit-adjacent and unfinished laps. Never fill it in."
    ),
    "sector1_s": "Sector time in seconds; NULL when the feed did not send it.",
    "sector2_s": "Sector time in seconds; NULL when the feed did not send it.",
    "sector3_s": "Sector time in seconds; NULL when the feed did not send it.",
    "is_personal_best": "The feed's own personal-best flag for this lap.",
    "compound": (
        "Tyre compound (`f1_domain.Compound` value). Taken from the lap event, "
        "falling back to the compound of the stint the lap belongs to."
    ),
    "tyre_age": (
        "Laps on this set of tyres at the END of this lap. Falls back to "
        "`lap_number - stint.start_lap + 1 + tyre_age_at_start` when the lap "
        "event carries no age, so it stays monotonic within a stint."
    ),
    "stint_number": (
        "1-based stint index. NULL when the source emitted no `StintStarted` "
        "events at all — stints are never invented from pit stops."
    ),
    "position": "Classification position as of this lap's end.",
    "gap_ahead_s": (
        "MEDIAN interval to the car ahead over the lap window, in seconds — "
        "the representative value for the lap, not a single instant. NULL "
        "when no interval samples fall inside the window (no gap channel, or "
        "the driver is leading)."
    ),
    "gap_ahead_min_s": "Closest the car ahead got during the lap window.",
    "gap_ahead_samples": (
        "How many interval samples the window contained — the quality signal "
        "behind `gap_ahead_s`. Zero means the gap is unknown, not large."
    ),
    "gap_to_leader_s": "Median gap to the race leader over the lap window.",
    "track_status": (
        "Most severe track status overlapping the lap window "
        "(`f1_domain.TrackStatusKind` value), 'green' when nothing else "
        "applies. Chequered ranks with green — see `f1_kernel.status`."
    ),
    "track_status_flags": (
        "EVERY non-green status overlapping the window, most severe first. A "
        "lap run partly yellow and partly under the safety car lists both, "
        "which is what lets the clean-lap filter flag both."
    ),
    "is_pit_in": "The driver entered the pit lane on this lap (in-lap).",
    "is_pit_out": "The driver left the pit lane on this lap (out-lap).",
    "provenance": (
        "'live' (feed-derived, live or replayed) or 'historical' "
        "(reconstructed by `f1_historical_adapter`). Same schema either way; "
        "historical has coarser timestamps — see DATA_CONTRACTS.md."
    ),
}

STINT_FRAME_SCHEMA: Schema = {
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "stint_number": pl.Int32(),
    "compound": pl.String(),
    "start_lap": pl.Int32(),
    "end_lap": pl.Int32(),
    "lap_count": pl.Int32(),
    "tyre_age_at_start": pl.Int32(),
    "provenance": pl.String(),
}
"""One row per (driver, stint), sorted by `(driver, stint_number)`."""

STINT_FRAME_DOCS: dict[str, str] = {
    "start_lap": "First lap of the stint (the out-lap, when it followed a stop).",
    "end_lap": (
        "Last lap of the stint: the lap before the next stint starts, or the "
        "driver's last observed lap for the final stint."
    ),
    "lap_count": (
        "Laps actually OBSERVED in `[start_lap, end_lap]` — not "
        "`end_lap - start_lap + 1`. The two differ when the feed missed a lap, "
        "and the observed count is the one an analytic can trust."
    ),
    "tyre_age_at_start": "Laps already on the tyre when the stint began (>0 = used set).",
}

TELEMETRY_FRAME_SCHEMA: Schema = {
    "session_year": pl.Int32(),
    "session_round": pl.Int32(),
    "session_type": pl.String(),
    "driver": pl.String(),
    "ts_utc": _UTC,
    "time_s": pl.Float64(),
    "lap_number": pl.Int32(),
    "distance_m": pl.Float64(),
    "lap_distance_m": pl.Float64(),
    "speed_kph": pl.Float64(),
    "throttle_pct": pl.Float64(),
    "brake_pct": pl.Float64(),
    "gear": pl.Int32(),
    "rpm": pl.Int32(),
    "drs": pl.Int32(),
    "drs_open": pl.Boolean(),
    "provenance": pl.String(),
}
"""One row per telemetry sample for ONE driver, sorted by `ts_utc`."""

TELEMETRY_FRAME_DOCS: dict[str, str] = {
    "time_s": "Seconds since this driver's first telemetry sample in the session.",
    "lap_number": (
        "Lap the sample falls in, from the lap windows in the lap frame. NULL "
        "for samples outside any known lap (before the first crossing, in the "
        "pits, or when lap data is missing)."
    ),
    "distance_m": (
        "Cumulative distance INTEGRATED from the speed channel — F1's car "
        "telemetry carries no distance channel. Trapezoidal, so it drifts a "
        "little over a session and is not survey-accurate; sample gaps longer "
        "than `MAX_INTEGRATION_GAP_S` contribute nothing rather than inventing "
        "distance across a hole in the stream."
    ),
    "lap_distance_m": (
        "Distance since the first sample OF THIS LAP — the x-axis a "
        "braking-point comparison runs on. Zero at the lap's first sample, "
        "which is near but not exactly the timing line."
    ),
    "brake_pct": (
        "0-100. Some seasons send this as effectively boolean (0/100) and "
        "FastF1's historical telemetry is boolean, so treat it as 'braking or "
        "not' unless you have verified otherwise for that season."
    ),
    "drs": "Raw feed DRS code: 0/1 off, 8 eligible, 10/12/14 open.",
    "drs_open": "Derived from `drs`: the flap is actually open (codes 10/12/14).",
}

DRS_OPEN_CODES: frozenset[int] = frozenset({10, 12, 14})
"""Raw DRS codes meaning "flap open" (8 is *eligible*, not open)."""


def empty_frame(schema: Schema) -> pl.DataFrame:
    """A zero-row frame with exactly `schema`. Returned instead of raising
    when a session has no data of that kind, so callers can concatenate and
    aggregate without special-casing emptiness."""
    return pl.DataFrame(schema=schema)
