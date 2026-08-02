"""The vocabulary: `Anomaly` and `AnomalyKind` — Phase 5.1's contract the way
`DomainEvent` was Phase 1's and the frame schemas were Phase 2's.

`detect_anomalies` (`f1_why.detect`) is the only thing that builds these;
this module just declares the shape everything else (a future evidence
bundler, an LLM narrator, a UI) reads.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import polars as pl

Schema = dict[str, pl.DataType]

DetailValue = float | int | str | bool | None
"""What a `summary` dict's values may hold — plain JSON-friendly scalars,
never a nested structure. Keeping this flat is what makes `summary`
"machine-readable": a consumer can read a key it knows about with no
schema of its own to maintain."""


class AnomalyKind(StrEnum):
    """What kind of thing happened. Every member documents `magnitude`'s
    unit and how `drivers` is populated/ordered — the two fields whose
    meaning otherwise depends entirely on the kind."""

    PACE_LOSS = "pace_loss"
    """A driver's lap was a robust outlier versus their own recent clean
    pace in the same stint (fuel-corrected), and slower — never faster.
    `magnitude` is seconds slower than the trend. `drivers` is one driver."""

    POSITION_CHANGE = "position_change"
    """An on-track overtake, not a pit-cycle reshuffle. `magnitude` is
    places changed (usually 1). `drivers` is `(gainer, loser)` when a clean
    one-for-one swap was identified, else the single driver whose
    classification moved with no isolable rival (a multi-car shuffle)."""

    PIT_ANOMALY = "pit_anomaly"
    """A green-flag pit stop's measured cost was well outside the
    calibrated norm for that circuit. `magnitude` is `abs(observed -
    expected)` seconds; `summary` says which direction. `drivers` is one
    driver."""

    UNDERCUT_RESOLVED = "undercut_resolved"
    """A real stop-pair duel this session's own walk-forward prediction
    spoke to has resolved (both cars have completed their post-stop lap).
    `magnitude` is the realized margin, seconds. `drivers` is `(attacker,
    defender)` — the attacker is always the car that was behind at the
    decision lap, matching `f1_kernel.undercut`'s own convention."""

    DEG_CLIFF = "deg_cliff"
    """A stint's degradation fit found a non-linear (quadratic) falloff on
    top of the linear rate. `magnitude` is the extra time, seconds, the
    curvature term implies at the last lap the fit used, versus the pure
    linear extrapolation. `drivers` is one driver."""

    OFF_TRACK = "off_track"
    """A lock-up/off-track proxy: one mini-sector's (sector 1/2/3) time was
    a robust outlier versus the driver's own recent clean pace in that
    sector, in the same stint — localized, unlike `PACE_LOSS`'s whole-lap
    view. `magnitude` is seconds slower than the sector trend. `drivers` is
    one driver."""


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One thing worth explaining. Detection only — this carries no
    evidence bundle and no narration, both later Phase 5 prompts' jobs."""

    kind: AnomalyKind
    lap_number: int | None
    """The lap the anomaly is anchored to — `None` only when no single lap
    applies (never the case for any kind this module currently emits)."""
    anomaly_time: datetime
    """UTC instant the anomaly is anchored to (a lap's `lap_end_utc`, or the
    later of a pair's for a two-driver kind) — what a future Delay Sync
    gate would compare against, per CLAUDE.md's Phase 5 spoiler-safety
    invariant. Detection itself does no gating; it is pure over frames."""
    drivers: tuple[str, ...]
    """One or two racing numbers. See `AnomalyKind` for the meaning and
    order per kind."""
    magnitude: float
    """How big this was, in the kind's own unit (seconds for every kind
    except `POSITION_CHANGE`, which is places) — see `AnomalyKind`. Always
    non-negative."""
    salience: float
    """A cross-kind, bounded-in-`[0, 1)` ranking score so a consumer can
    sort/limit without knowing each kind's own unit — see
    `f1_why.detect`'s salience model. Not a probability; a race-defining
    moment saturates towards 1, a minor wobble sits near 0."""
    summary: dict[str, DetailValue]
    """The machine-readable "what changed": the specific numbers this
    anomaly was computed from (observed/expected/delta, confidence,
    compound, ...), keyed per kind. Never prose — narrating this is a later
    Phase 5 prompt's job, not this one's."""
    reasons: tuple[str, ...]
    """Caveats a consumer should not discard the honesty of: a thin
    degradation fit, a provisional pit loss, a low-confidence undercut
    call. Mirrors `f1_kernel.undercut.CrossoverResult.reasons`."""


ANOMALY_SCHEMA: Schema = {
    "kind": pl.String(),
    "lap_number": pl.Int32(),
    "anomaly_time": pl.Datetime("us", "UTC"),
    "drivers": pl.List(pl.String()),
    "magnitude": pl.Float64(),
    "salience": pl.Float64(),
    "summary_json": pl.String(),
    "reasons": pl.String(),
}


def anomalies_to_frame(anomalies: Sequence[Anomaly]) -> pl.DataFrame:
    """`Anomaly`s flattened to one row each, for a report or further
    aggregation — the same role every Phase 2 analytic's own `*_to_frame`
    plays. `summary` is JSON-encoded (its shape varies by kind, unlike a
    frame's columns) and `reasons` joins to '; ', matching
    `f1_kernel.undercut.crossover_results_to_frame`'s own convention."""
    if not anomalies:
        return pl.DataFrame(schema=ANOMALY_SCHEMA)
    rows = [
        {
            "kind": anomaly.kind.value,
            "lap_number": anomaly.lap_number,
            "anomaly_time": anomaly.anomaly_time,
            "drivers": list(anomaly.drivers),
            "magnitude": anomaly.magnitude,
            "salience": anomaly.salience,
            "summary_json": json.dumps(anomaly.summary, sort_keys=True),
            "reasons": "; ".join(anomaly.reasons),
        }
        for anomaly in anomalies
    ]
    return pl.DataFrame(rows, schema=ANOMALY_SCHEMA)


__all__ = [
    "ANOMALY_SCHEMA",
    "Anomaly",
    "AnomalyKind",
    "DetailValue",
    "anomalies_to_frame",
]
