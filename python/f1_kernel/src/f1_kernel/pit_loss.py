"""Pit-loss calibration: what a green-flag stop actually costs, per circuit.

`docs/PHASE2.md` sketched the method when the circuit files shipped with
provisional figures: *(in-lap + out-lap) - 2 x the driver's clean-lap
baseline, over every green-flag stop in a season, median per circuit*. This
module implements that with one refinement: instead of a single race-wide
baseline, each stop is compared against its own NEIGHBOURING clean laps —
the median of the clean laps just before the stop (same outgoing stint,
i.e. old-tyre pace) plus the median of the clean laps just after the
out-lap (new stint, warmed fresh-tyre pace):

    pit_loss = (t_in + t_out) - (median_pre + median_post)

all in fuel-corrected time. The in-lap runs on the same old tyres as the
pre-window and the out-lap rejoins onto the same fresh set as the
post-window, so compound changes and tyre wear across the stop cancel to
first order — which a single race-wide baseline cannot do — and using
neighbours keeps track evolution and temperature drift second-order.

**What the number contains.** By construction this is the TOTAL cost of a
routine stop versus staying out: pit-lane transit, the stationary time, AND
the average out-lap warm-up (the out-lap is compared against warmed fresh
pace). Lap times alone cannot split transit from warm-up, so we do not
pretend to — `KernelParams.undercut_out_lap_penalty_s` defaults to 0 for
exactly this reason. It is also why a calibrated value here is the right
quantity for crossover projections: every simulated stop pays this once.

**Green flags only.** A stop under a safety car or VSC costs far less in
race time — that asymmetry is the whole point of the undercut analytic —
so any stop whose in-lap or out-lap window touched ANY non-green status is
excluded, as are drive-throughs and penalty stops (no stint change) and
double-stops. Exclusions are counted per reason, never silent.

`calibrate_pit_loss` is the offline (but re-runnable) entry point —
`python/f1_kernel/tools/calibrate_pit_loss.py` runs it over the backfill,
writes `calibration/pit_loss.json`, and patches the shipped circuit files
after review. The kernel never calls it at analysis time.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from f1_domain import SessionKey, TrackStatusKind
from f1_kernel.circuit import circuit_for_event
from f1_kernel.cleaning import annotate_laps
from f1_kernel.fuel import fuel_correct
from f1_kernel.params import KernelParams
from f1_kernel.sources import EventSource

MAD_TO_SIGMA = 1.4826
"""See `f1_kernel.cleaning.MAD_TO_SIGMA` — the same normal-consistency scale."""

MEDIAN_SE_FACTOR = 1.2533
"""Asymptotic std-error of a median is ~1.2533 * sigma / sqrt(n) for normal
data — how a MAD spread becomes an uncertainty on the reported median."""

DEFAULT_NEIGHBOR_WINDOW_LAPS = 5
DEFAULT_MIN_NEIGHBOR_LAPS = 2
DEFAULT_PLAUSIBLE_RANGE_S = (5.0, 60.0)
"""A green-flag stop outside this band is a data problem (a missed lap, a
red-flag tyre change leaking through), not a measurement."""


@dataclass(frozen=True, slots=True)
class PitLossSample:
    """One green-flag stop's measured loss — the unit of evidence."""

    session_year: int
    session_round: int
    session_type: str
    event_name: str | None
    driver: str
    in_lap: int
    out_lap: int
    pit_loss_s: float
    """(t_in + t_out) - (median_pre + median_post), fuel-corrected seconds."""
    n_pre_laps: int
    n_post_laps: int
    """How many clean neighbour laps stood behind each baseline median."""


@dataclass(frozen=True, slots=True)
class CircuitPitLoss:
    """One circuit's aggregated estimate, with its own uncertainty."""

    circuit_key: str
    """`circuit_id` when a shipped circuit file matches the event, else the
    slugified event name — so circuits we do not ship still get a number."""
    circuit_id: str | None
    event_names: tuple[str, ...]
    pit_loss_s: float
    """Median over every measured stop at this circuit."""
    mad_s: float
    """Raw median-absolute-deviation of the samples, in seconds."""
    uncertainty_s: float
    """~1 standard error on the median: 1.2533 * (1.4826 * MAD) / sqrt(n)."""
    n_stops: int
    n_sessions: int
    seasons: tuple[int, ...]
    thin: bool
    """True when `n_stops` is below the calibration's floor — report it,
    but keep the circuit file provisional rather than calibrating on air."""


@dataclass(frozen=True, slots=True)
class PitLossCalibrationResult:
    """Every circuit's estimate plus everything needed to audit the run."""

    circuits: tuple[CircuitPitLoss, ...]
    n_samples: int
    n_sessions: int
    suggested_default_pit_loss_s: float | None
    """Median of the non-thin per-circuit medians — the candidate for
    `KernelParams.default_pit_loss_s` (updated by hand after review). None
    when no circuit cleared the sample floor."""
    excluded: dict[str, int]
    """Stops dropped, per reason — the honesty ledger."""
    method: str
    samples: tuple[PitLossSample, ...] = field(repr=False)


_NON_GREEN = frozenset(
    {
        TrackStatusKind.SC.value,
        TrackStatusKind.VSC.value,
        TrackStatusKind.YELLOW.value,
        TrackStatusKind.RED.value,
    }
)
"""Track-status flag values that disqualify a stop's in/out lap."""


def pit_loss_samples(
    laps: pl.DataFrame,
    params: KernelParams,
    *,
    event_name: str | None = None,
    total_laps: int | None = None,
    neighbor_window_laps: int = DEFAULT_NEIGHBOR_WINDOW_LAPS,
    min_neighbor_laps: int = DEFAULT_MIN_NEIGHBOR_LAPS,
    plausible_range_s: tuple[float, float] = DEFAULT_PLAUSIBLE_RANGE_S,
) -> tuple[list[PitLossSample], Counter[str]]:
    """Measure every green-flag stop in one session's FULL lap frame.

    Takes the unfiltered lap frame (in/out laps are excluded from
    `clean_laps`, and they are the whole point here); annotation and fuel
    correction happen internally. Returns the samples plus a counter of
    stops excluded per reason — a session where every stop happened under a
    safety car yields zero samples and says why, rather than nothing.

    Pure and deterministic: same frame and params in, same samples out.
    """
    if laps.is_empty():
        return [], Counter()

    annotated = annotate_laps(laps, params)
    corrected = fuel_correct(annotated, params, total_laps=total_laps)

    samples: list[PitLossSample] = []
    excluded: Counter[str] = Counter()
    low, high = plausible_range_s

    for (driver_value,), group in corrected.group_by(["driver"], maintain_order=True):
        driver = str(driver_value)
        rows = {int(row["lap_number"]): row for row in group.sort("lap_number").to_dicts()}
        for lap_number, row in rows.items():
            if not row["is_pit_in"]:
                continue
            out_row = rows.get(lap_number + 1)
            if out_row is None or not out_row["is_pit_out"]:
                excluded["no_out_lap"] += 1
                continue
            if out_row["is_pit_in"]:
                excluded["double_stop"] += 1
                continue
            in_stint, out_stint = row["stint_number"], out_row["stint_number"]
            if in_stint is None or out_stint is None or in_stint == out_stint:
                # No stint change = a drive-through or penalty, not a tyre stop.
                excluded["no_stint_change"] += 1
                continue
            if _touches_non_green(row) or _touches_non_green(out_row):
                excluded["not_green"] += 1
                continue
            t_in = row["lap_time_fuel_corrected_s"]
            t_out = out_row["lap_time_fuel_corrected_s"]
            if not isinstance(t_in, int | float) or not isinstance(t_out, int | float):
                excluded["missing_lap_time"] += 1
                continue

            pre = _neighbor_median(
                rows,
                range(lap_number - neighbor_window_laps, lap_number),
                stint=int(in_stint),
            )
            post = _neighbor_median(
                rows,
                range(lap_number + 2, lap_number + 2 + neighbor_window_laps),
                stint=int(out_stint),
            )
            if pre is None or pre[1] < min_neighbor_laps:
                excluded["too_few_pre_laps"] += 1
                continue
            if post is None or post[1] < min_neighbor_laps:
                excluded["too_few_post_laps"] += 1
                continue

            loss = (float(t_in) + float(t_out)) - (pre[0] + post[0])
            if not low <= loss <= high:
                excluded["implausible"] += 1
                continue

            samples.append(
                PitLossSample(
                    session_year=int(row["session_year"]),
                    session_round=int(row["session_round"]),
                    session_type=str(row["session_type"]),
                    event_name=event_name,
                    driver=driver,
                    in_lap=lap_number,
                    out_lap=lap_number + 1,
                    pit_loss_s=loss,
                    n_pre_laps=pre[1],
                    n_post_laps=post[1],
                )
            )

    return samples, excluded


def _touches_non_green(row: dict[str, object]) -> bool:
    flags = row["track_status_flags"]
    if flags is None:
        return False
    return any(flag in _NON_GREEN for flag in flags)  # type: ignore[union-attr]


def _neighbor_median(
    rows: dict[int, dict[str, object]], lap_range: range, *, stint: int
) -> tuple[float, int] | None:
    """Median fuel-corrected time over the CLEAN laps of `lap_range` that
    belong to `stint`, plus how many there were."""
    values: list[float] = []
    for lap_number in lap_range:
        row = rows.get(lap_number)
        if row is None or row["stint_number"] != stint or not row["is_clean"]:
            continue
        value = row["lap_time_fuel_corrected_s"]
        if isinstance(value, int | float):
            values.append(float(value))
    if not values:
        return None
    return float(np.median(values)), len(values)


def calibrate_pit_loss(
    source: EventSource,
    session_keys: Sequence[SessionKey],
    params: KernelParams,
    *,
    min_stops_per_circuit: int = 8,
    neighbor_window_laps: int = DEFAULT_NEIGHBOR_WINDOW_LAPS,
    min_neighbor_laps: int = DEFAULT_MIN_NEIGHBOR_LAPS,
) -> PitLossCalibrationResult:
    """Measure every green-flag stop in `session_keys` and aggregate per
    circuit (median, MAD, ~SE of the median).

    Sessions are grouped onto circuits by event name via
    `circuit_for_event`; events without a shipped circuit file still get an
    estimate under their slugified event name, so the calibration artifact
    covers the whole corpus even though only shipped files can be patched.

    Circuits with fewer than `min_stops_per_circuit` measured stops are
    reported with `thin=True`: their number is shown, but the circuit file
    should stay provisional — a median of three stops is an anecdote.

    Raises `ValueError` when not one stop anywhere could be measured;
    silently returning an empty result would look like a season without
    pit stops, which is a claim, not an absence of one.
    """
    from f1_kernel.load import lap_frame  # local: avoids a load->pit_loss import cycle

    event_names = _event_names_by_key(source)
    all_samples: list[PitLossSample] = []
    excluded: Counter[str] = Counter()
    by_circuit: dict[str, list[PitLossSample]] = defaultdict(list)
    circuit_ids: dict[str, str | None] = {}
    circuit_events: dict[str, set[str]] = defaultdict(set)
    sessions_by_circuit: dict[str, set[SessionKey]] = defaultdict(set)

    for key in session_keys:
        event_name = event_names.get(key)
        samples, session_excluded = pit_loss_samples(
            lap_frame(source, key),
            params,
            event_name=event_name,
            neighbor_window_laps=neighbor_window_laps,
            min_neighbor_laps=min_neighbor_laps,
        )
        excluded.update(session_excluded)
        if not samples:
            continue
        circuit = circuit_for_event(event_name)
        circuit_key = circuit.circuit_id if circuit is not None else _slug(event_name)
        circuit_ids[circuit_key] = circuit.circuit_id if circuit is not None else None
        if event_name:
            circuit_events[circuit_key].add(event_name)
        by_circuit[circuit_key].extend(samples)
        sessions_by_circuit[circuit_key].add(key)
        all_samples.extend(samples)

    if not all_samples:
        raise ValueError(
            "no green-flag stop anywhere in the given sessions could be measured "
            f"(exclusions: {dict(excluded) or 'none — no stops found at all'})"
        )

    circuits: list[CircuitPitLoss] = []
    for circuit_key in sorted(by_circuit):
        samples = by_circuit[circuit_key]
        losses = np.asarray([sample.pit_loss_s for sample in samples], dtype=float)
        median = float(np.median(losses))
        mad = float(np.median(np.abs(losses - median)))
        n = len(samples)
        uncertainty = MEDIAN_SE_FACTOR * MAD_TO_SIGMA * mad / math.sqrt(n)
        keys = sessions_by_circuit[circuit_key]
        circuits.append(
            CircuitPitLoss(
                circuit_key=circuit_key,
                circuit_id=circuit_ids[circuit_key],
                event_names=tuple(sorted(circuit_events[circuit_key])),
                pit_loss_s=median,
                mad_s=mad,
                uncertainty_s=uncertainty,
                n_stops=n,
                n_sessions=len(keys),
                seasons=tuple(sorted({k.year for k in keys})),
                thin=n < min_stops_per_circuit,
            )
        )

    solid = [circuit.pit_loss_s for circuit in circuits if not circuit.thin]
    return PitLossCalibrationResult(
        circuits=tuple(circuits),
        n_samples=len(all_samples),
        n_sessions=len({(s.session_year, s.session_round, s.session_type) for s in all_samples}),
        suggested_default_pit_loss_s=float(np.median(solid)) if solid else None,
        excluded=dict(excluded),
        method=(
            "per green-flag stop: (in-lap + out-lap) - (median of >="
            f"{min_neighbor_laps} clean laps in the {neighbor_window_laps} laps before the "
            f"stop, same stint + same after the out-lap, new stint), fuel-corrected; "
            "stops under SC/VSC/yellow/red, drive-throughs (no stint change) and "
            "double-stops excluded; per-circuit value is the median across stops, "
            "uncertainty ~1.2533 * 1.4826 * MAD / sqrt(n). Includes average out-lap "
            "warm-up by construction — see f1_kernel.pit_loss module docs."
        ),
        samples=tuple(all_samples),
    )


def _event_names_by_key(source: EventSource) -> dict[SessionKey, str | None]:
    try:
        refs = source.sessions()
    except Exception:
        return {}
    return {ref.session_key: ref.event_name for ref in refs}


def _slug(event_name: str | None) -> str:
    if not event_name:
        return "unknown_circuit"
    return re.sub(r"[^a-z0-9]+", "_", event_name.casefold()).strip("_")


__all__ = [
    "DEFAULT_MIN_NEIGHBOR_LAPS",
    "DEFAULT_NEIGHBOR_WINDOW_LAPS",
    "DEFAULT_PLAUSIBLE_RANGE_S",
    "CircuitPitLoss",
    "PitLossCalibrationResult",
    "PitLossSample",
    "calibrate_pit_loss",
    "pit_loss_samples",
]
