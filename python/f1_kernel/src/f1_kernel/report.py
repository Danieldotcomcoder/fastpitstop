"""`analyze_session`: the one call that runs the whole kernel.

Every earlier Phase 2 prompt built one analytic (clean-lap filter, fuel
correction, degradation, undercut/overcut, braking drift), each tested and
callable on its own. This module is the seam CLAUDE.md's Prompt 2.7 asks
for: a single typed `KernelReport` that chains them in the documented
order — clean -> fuel-correct -> degradation -> crossover -> braking-drift
— so a caller (a backtest, a CLI, eventually Phase 3) does not have to
re-derive the wiring `f1_kernel.undercut_eval` and every `tools/backtest_*.py`
script already hand-rolled.

Two entry points, mirroring the `frames`/`load` split used everywhere else
in the kernel:

- **`analyze_session_from_frames`** — the PURE core. Takes an already-built
  `SessionFrames` (plus optional per-driver telemetry for braking) and
  params; no I/O, no clock, no network. "Pure over loaded frames," per the
  prompt.
- **`analyze_session`** — the loading wrapper, the one most callers use:
  given a source and a session key, load the frames (and, opt-in, the
  telemetry) and delegate to the pure core.

`LiveKernelSession` is the "live variant" the prompt also asks for: a
small stateful accumulator that holds a growing `DomainEvent` stream and
recomputes a fresh `KernelReport` on every `push`. It is the one kernel
object that is not frozen — Phase 3 needs something to call after every
completed lap — but each `push` is still a pure recomputation over exactly
the events handed to it so far, never a clock or a socket. It deliberately
does not attempt true incremental deltas: a lap frame is small enough
(one race is a few thousand rows at most) that recomputing it from scratch
on every push is simply not worth the complexity a stateful diff would add.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import polars as pl

from f1_domain import DomainEvent, LapCounterUpdated, SessionKey
from f1_kernel.braking import BrakingDrift, braking_drift, braking_points
from f1_kernel.cleaning import annotate_laps, clean_lap_summary
from f1_kernel.degradation import (
    DegradationFit,
    fit_degradation,
    summarize_degradation_by_compound,
)
from f1_kernel.frames import (
    SessionFrames,
    session_frames_from_events,
    telemetry_frame_from_events,
)
from f1_kernel.fuel import fuel_correct
from f1_kernel.load import session_frames, telemetry_frame
from f1_kernel.params import KernelParams
from f1_kernel.rejoin import RejoinProjection, rejoin_snapshot
from f1_kernel.sources import EventSource
from f1_kernel.undercut import CrossoverResult, undercut_snapshot


@dataclass(frozen=True, slots=True)
class KernelReport:
    """Everything one `analyze_session` call produces, in the documented
    stage order. Every field that is itself an analytic's output already
    carries that analytic's own confidence/reliability signal
    (`DegradationFit.reliability`, `CrossoverResult.confidence`,
    `BrakingDrift.reliability`) — this report assembles those, it does not
    invent a new number on top of them."""

    session_key: SessionKey
    event_name: str | None
    params: KernelParams
    frames: SessionFrames
    """The loaded lap/stint/circuit frames every stage below was built from."""

    annotated_laps: pl.DataFrame
    """Every lap, clean or not, with its exclusion reasons — `annotate_laps`."""
    clean_laps: pl.DataFrame
    """Only the laps later stages are fitted on — `annotated_laps` filtered
    to `is_clean`, same columns."""
    lap_summary: pl.DataFrame
    """Per-driver clean-lap counts and exclusion breakdown — `clean_lap_summary`."""
    fuel_corrected_laps: pl.DataFrame
    """`clean_laps` plus `fuel_load_kg` / `lap_time_fuel_corrected_s`."""

    degradation_fits: tuple[DegradationFit, ...]
    """One fit per (driver, stint, compound) with enough clean laps — see
    `f1_kernel.degradation.fit_degradation`."""
    degradation_by_compound: pl.DataFrame
    """`summarize_degradation_by_compound`'s per-compound rollup."""

    total_laps: int | None
    """Race length used by the fuel model and the crossover horizon — the
    frame's own observed max lap number unless the caller overrode it."""
    decision_lap: int | None
    """The lap `crossovers` were projected from. None when no pair could be
    evaluated at all (see `notes`)."""
    crossovers: tuple[CrossoverResult, ...]
    """One `undercut_window` call per adjacent pair at `decision_lap`,
    walk-forward safe (`f1_kernel.undercut.undercut_snapshot`)."""
    rejoins: tuple[RejoinProjection, ...]
    """Where each car comes out if it boxes at the end of the next lap,
    from the same decision lap (`f1_kernel.rejoin.rejoin_snapshot`)."""

    braking_drift: tuple[BrakingDrift, ...]
    """Empty unless telemetry was supplied for at least one driver — see
    `braking_drivers` and `analyze_session`'s `braking_drivers` argument.
    Telemetry is large and resampled (DATA_CONTRACTS.md), so this stage is
    opt-in, unlike the other four."""
    braking_drivers: tuple[str, ...]
    """Drivers braking-point drift actually produced a fit for."""

    notes: tuple[str, ...]
    """Anything about this run worth surfacing that is not itself a number:
    missing circuit metadata, an empty stage, braking skipped."""

    def summary(self) -> str:
        """A short human-readable recap — what a CLI or a log line prints,
        never what an analytic decides anything from."""
        key = self.session_key
        header = f"{key.year} R{key.round:02d} {key.session_type.value}"
        if self.event_name:
            header += f" ({self.event_name})"
        total = self.frames.laps.height
        clean = self.clean_laps.height
        lap_line = (
            f"laps: {total} total, {clean} clean ({clean / total:.0%})" if total else "laps: 0"
        )
        lines = [
            header,
            lap_line,
            f"degradation fits: {len(self.degradation_fits)}",
            f"crossover pairs at lap {self.decision_lap}: {len(self.crossovers)}",
            f"braking drift fits: {len(self.braking_drift)} "
            f"({len(self.braking_drivers)} driver(s))",
        ]
        if self.notes:
            lines.append("notes: " + "; ".join(self.notes))
        return "\n".join(lines)


def analyze_session_from_frames(
    frames: SessionFrames,
    params: KernelParams,
    *,
    telemetry: Mapping[str, pl.DataFrame] | None = None,
    decision_lap: int | None = None,
    total_laps: int | None = None,
) -> KernelReport:
    """The pure core: clean -> fuel-correct -> degradation -> crossover ->
    braking-drift over frames already loaded. No I/O, no clock, no network;
    same frames/params/telemetry in, an identical report out.

    `telemetry` is `{driver: TELEMETRY_FRAME_SCHEMA frame}` for whichever
    drivers braking-point drift should run for — omit it (the default) to
    skip that stage entirely, since telemetry is the one input that is
    genuinely expensive to load. Every other stage always runs, even over
    an empty lap frame: each analytic already degrades to an empty,
    correctly-schemed result rather than raising (that discipline is what
    lets a caller run this over every session in a backtest without
    special-casing the thin ones).
    """
    notes: list[str] = []
    if frames.laps.is_empty():
        notes.append("no laps in frames — every stage below is empty")
    elif frames.circuit is None:
        notes.append(
            "no shipped circuit metadata for this event — pit loss falls back to "
            "KernelParams.default_pit_loss_s, and braking-point drift cannot run"
        )

    resolved_total_laps = _resolve_total_laps(frames, total_laps)

    annotated = annotate_laps(frames.laps, params)
    clean = annotated.filter(pl.col("is_clean"))
    summary = clean_lap_summary(frames.laps, params)
    fuel_corrected = fuel_correct(clean, params, total_laps=resolved_total_laps)

    fits = fit_degradation(fuel_corrected, params)
    by_compound = summarize_degradation_by_compound(fits)
    if not frames.laps.is_empty() and not fits:
        notes.append("no degradation fit produced — check clean-lap coverage per stint")

    crossovers = undercut_snapshot(
        frames, params, decision_lap=decision_lap, total_laps=resolved_total_laps
    )
    resolved_decision_lap = crossovers[0].decision_lap if crossovers else decision_lap
    if not frames.laps.is_empty() and not crossovers:
        notes.append("no undercut/overcut pair found at the decision lap")
    rejoin_lap = (
        resolved_decision_lap
        if resolved_decision_lap is not None
        else _latest_shared_lap(frames.laps)
    )
    rejoins = (
        rejoin_snapshot(frames, params, decision_lap=rejoin_lap) if rejoin_lap is not None else []
    )

    braking_fits: list[BrakingDrift] = []
    braking_drivers: list[str] = []
    if telemetry:
        if frames.circuit is None:
            notes.append(
                f"telemetry supplied for {len(telemetry)} driver(s) but no shipped circuit "
                "metadata for this event — braking-point drift skipped"
            )
        else:
            for driver in sorted(telemetry):
                points = braking_points(telemetry[driver], frames.circuit, params)
                drift = braking_drift(points, frames.stints, params)
                if drift:
                    braking_drivers.append(driver)
                    braking_fits.extend(drift)

    return KernelReport(
        session_key=frames.session_key,
        event_name=frames.event_name,
        params=params,
        frames=frames,
        annotated_laps=annotated,
        clean_laps=clean,
        lap_summary=summary,
        fuel_corrected_laps=fuel_corrected,
        degradation_fits=tuple(fits),
        degradation_by_compound=by_compound,
        total_laps=resolved_total_laps,
        decision_lap=resolved_decision_lap,
        crossovers=tuple(crossovers),
        rejoins=tuple(rejoins),
        braking_drift=tuple(braking_fits),
        braking_drivers=tuple(braking_drivers),
        notes=tuple(notes),
    )


def analyze_session(
    source: EventSource,
    session_key: SessionKey,
    params: KernelParams | None = None,
    *,
    event_name: str | None = None,
    braking_drivers: Collection[str] | None = None,
    decision_lap: int | None = None,
    total_laps: int | None = None,
) -> KernelReport:
    """Load one session from `source` and run the full kernel over it — the
    entry point most callers want.

    `braking_drivers` opts specific drivers into the braking-point-drift
    stage (it pulls their telemetry via `source`, one driver at a time);
    omit it to skip that stage, since telemetry is large and resampled for
    historical sources (DATA_CONTRACTS.md). This is the "load, then pass
    frames in" wrapper `f1_kernel.load` uses everywhere else —
    `analyze_session_from_frames` is the pure core underneath it.
    """
    resolved_params = params if params is not None else KernelParams()
    frames = session_frames(source, session_key, event_name=event_name)
    telemetry = (
        {driver: telemetry_frame(source, session_key, driver) for driver in braking_drivers}
        if braking_drivers
        else None
    )
    return analyze_session_from_frames(
        frames,
        resolved_params,
        telemetry=telemetry,
        decision_lap=decision_lap,
        total_laps=total_laps,
    )


def _resolve_total_laps(frames: SessionFrames, total_laps: int | None) -> int | None:
    """The race length fuel correction and the crossover horizon both use —
    resolved once here so the two stages can never disagree about it (see
    `f1_kernel.fuel.fuel_correct`'s own docstring for why a mismatch would
    bias the fuel model)."""
    if total_laps is not None:
        return total_laps
    if frames.laps.is_empty():
        return None
    value = frames.laps["lap_number"].max()
    return int(value) if isinstance(value, int | float) else None


@dataclass(slots=True)
class LiveKernelSession:
    """The live variant: holds a growing `DomainEvent` stream for one
    session and recomputes a fresh `KernelReport` from everything
    accumulated so far on every `push` — the shape Phase 3 drives after
    each completed lap (or each batch of records normalized through
    `f1_pipeline`).

    The only kernel object that is not frozen, and deliberately so: every
    other analytic in this package is a pure function with no memory
    between calls, but a live consumer needs something to hold "which
    events have I seen" between lap updates. What stays true to CLAUDE.md's
    purity invariant is that `push` never touches a clock, a socket or a
    database itself — it only accumulates the events it was handed and
    reruns the same pure `analyze_session_from_frames` pipeline over them.
    """

    session_key: SessionKey
    params: KernelParams = field(default_factory=KernelParams)
    event_name: str | None = None
    braking_drivers: frozenset[str] = frozenset()
    total_laps: int | None = None
    """The race distance, when the caller knows it (a replay of a finished
    race does). Otherwise read from the stream's own lap counter. Never
    inferred from the laps pushed so far: see `race_distance_laps`."""
    _events: list[DomainEvent] = field(default_factory=list[DomainEvent], repr=False)

    @property
    def events(self) -> tuple[DomainEvent, ...]:
        """Everything accumulated so far, for a caller that wants to persist
        or inspect the raw stream."""
        return tuple(self._events)

    def push(self, events: Iterable[DomainEvent]) -> KernelReport:
        """Fold in the next batch of already-normalized `DomainEvent`s for
        THIS session (events for another session are ignored, the same
        defensive filter `f1_kernel.sources.MemorySource` applies) and
        return a fresh report over everything accumulated so far.
        """
        self._events.extend(event for event in events if event.session_key == self.session_key)
        frames = session_frames_from_events(
            self._events, self.session_key, event_name=self.event_name
        )
        telemetry = (
            {
                driver: telemetry_frame_from_events(
                    self._events, self.session_key, driver, laps=frames.laps
                )
                for driver in sorted(self.braking_drivers)
            }
            if self.braking_drivers
            else None
        )
        total_laps = self.total_laps if self.total_laps is not None else _race_counter(self._events)
        return analyze_session_from_frames(
            frames, self.params, telemetry=telemetry, total_laps=total_laps
        )


def _latest_shared_lap(laps: pl.DataFrame) -> int | None:
    """The latest lap at least two cars have completed: the decision lap
    `undercut_snapshot` would pick, for when it found no pair at all."""
    if laps.is_empty():
        return None
    shared = (
        laps.filter(pl.col("lap_end_utc").is_not_null())
        .group_by("lap_number")
        .agg(pl.col("driver").n_unique().alias("n"))
        .filter(pl.col("n") >= 2)
    )
    if shared.is_empty():
        return None
    found = shared["lap_number"].max()
    return found if isinstance(found, int) else None


def _race_counter(events: Sequence[DomainEvent]) -> int | None:
    """The feed's own race distance, if the stream carries its lap counter.
    Deliberately NOT the furthest lap completed: mid-race that is the slice,
    the exact mis-reading `LiveKernelSession.total_laps` exists to prevent."""
    for event in reversed(events):
        if isinstance(event, LapCounterUpdated) and event.total_laps is not None:
            return event.total_laps
    return None


__all__ = [
    "KernelReport",
    "LiveKernelSession",
    "analyze_session",
    "analyze_session_from_frames",
]
