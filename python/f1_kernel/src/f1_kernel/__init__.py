"""f1_kernel: the Phase 2 analytics kernel — the moat.

Everything public stops at "render the feed"; this derives state the feed
does not carry. It is built as PURE FUNCTIONS over typed frames: you LOAD
data (from the historical parquet corpus, from the TimescaleDB event log,
or from events you already hold) and then PASS FRAMES IN. No analytic
touches a database, a network or a clock, so the same input always gives
the same output — which is what makes any of it backtestable.

The shape of the package:

- `schemas` — the STABLE frame schemas (lap / stint / telemetry). Phase 2's
  contract, the way `DomainEvent` was Phase 1's.
- `sources` + `load` — where events come from, and the `lap_frame` /
  `stint_frame` / `telemetry_frame` / `session_frames` entry points.
- `frames` — the pure event->frame transforms behind those entry points.
- `status` — track status as a queryable timeline (live's own signal, or
  reconstructed from race control for historical sessions).
- `params` — `KernelParams`: every tunable constant, typed, overridable,
  with per-field provenance. Most defaults are PROVISIONAL until calibrated.
- `circuit` — pit loss, corner geometry and DRS zones, derived from real
  session data rather than guessed.
- `cleaning` — the clean-lap filter: `clean_laps` for analytics,
  `annotate_laps` for a UI that must explain exclusions.
- `braking` — braking-point drift: `braking_points` finds each lap's
  sustained brake-application distance per corner from telemetry;
  `braking_drift` fits how that point moves across a stint's tyre age.
- `backtest` — running an analytic over a corpus of sessions and tabulating
  the result. How every analytic in Phase 2 is tested.
- `report` — `analyze_session`: the whole kernel behind one call, chaining
  clean -> fuel-correct -> degradation -> crossover -> braking-drift into
  one typed `KernelReport`. `LiveKernelSession` is the incremental variant
  a growing live event stream drives.

Typical use:

    from f1_domain import SessionKey, SessionType
    from f1_kernel import HistoricalSource, KernelParams, clean_laps, lap_frame

    source = HistoricalSource("data/historical")
    key = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
    laps = lap_frame(source, key)
    good = clean_laps(laps, KernelParams())
"""

from f1_kernel.backtest import (
    Analytic,
    Backtest,
    BacktestError,
    BacktestResult,
    SessionFailure,
)
from f1_kernel.braking import (
    BRAKING_DRIFT_SCHEMA,
    BRAKING_FRAME_SCHEMA,
    BrakingDrift,
    braking_drift,
    braking_drift_to_frame,
    braking_points,
)
from f1_kernel.circuit import (
    CIRCUITS_DIR,
    Circuit,
    Corner,
    DrsZone,
    circuit_for_event,
    list_circuits,
    load_circuit,
)
from f1_kernel.cleaning import (
    ANNOTATED_LAP_FRAME_SCHEMA,
    REASON_COLUMNS,
    annotate_laps,
    clean_lap_summary,
    clean_laps,
)
from f1_kernel.degradation import (
    DEGRADATION_FIT_SCHEMA,
    CompoundOffsetCalibrationResult,
    CompoundOffsetSample,
    DegradationFit,
    Reliability,
    calibrate_compound_offsets,
    calibrate_compound_offsets_from_frames,
    degradation_fits_to_frame,
    fit_degradation,
    summarize_degradation_by_compound,
)
from f1_kernel.frames import (
    SessionFrames,
    lap_frame_from_events,
    session_frames_from_events,
    stint_frame_from_events,
    telemetry_frame_from_events,
)
from f1_kernel.fuel import (
    FUEL_CORRECTED_LAP_FRAME_SCHEMA,
    FuelCalibrationResult,
    FuelStintEstimate,
    calibrate_fuel_coefficient,
    calibrate_fuel_coefficient_from_frames,
    estimate_fuel_load_kg,
    fuel_correct,
    fuel_correction_delta_s,
)
from f1_kernel.load import lap_frame, session_frames, stint_frame, telemetry_frame
from f1_kernel.params import (
    PARAM_PROVENANCE,
    KernelParams,
    ParamProvenance,
    describe_params,
)
from f1_kernel.pit_loss import (
    CircuitPitLoss,
    PitLossCalibrationResult,
    PitLossSample,
    calibrate_pit_loss,
    pit_loss_samples,
)
from f1_kernel.report import (
    KernelReport,
    LiveKernelSession,
    analyze_session,
    analyze_session_from_frames,
)
from f1_kernel.schemas import (
    LAP_FRAME_DOCS,
    LAP_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    TELEMETRY_FRAME_SCHEMA,
    empty_frame,
)
from f1_kernel.sources import (
    EventSource,
    HistoricalSource,
    MemorySource,
    SessionRef,
    StoreSource,
    list_historical_sessions,
    session_type_from_label,
)
from f1_kernel.status import (
    StatusInterval,
    status_during,
    statuses_during,
    track_status_timeline,
)
from f1_kernel.track_outline import TrackOutline, derive_track_outline
from f1_kernel.undercut import (
    CROSSOVER_RESULT_SCHEMA,
    Confidence,
    CrossoverKind,
    CrossoverResult,
    DriverStintState,
    WindowState,
    crossover_results_to_frame,
    overcut_window,
    undercut_snapshot,
    undercut_window,
)
from f1_kernel.undercut_eval import (
    StopPairOutcome,
    evaluate_session_stops,
    evaluate_session_stops_rows,
)

__all__ = [
    "ANNOTATED_LAP_FRAME_SCHEMA",
    "BRAKING_DRIFT_SCHEMA",
    "BRAKING_FRAME_SCHEMA",
    "CIRCUITS_DIR",
    "CROSSOVER_RESULT_SCHEMA",
    "DEGRADATION_FIT_SCHEMA",
    "FUEL_CORRECTED_LAP_FRAME_SCHEMA",
    "LAP_FRAME_DOCS",
    "LAP_FRAME_SCHEMA",
    "PARAM_PROVENANCE",
    "REASON_COLUMNS",
    "STINT_FRAME_SCHEMA",
    "TELEMETRY_FRAME_SCHEMA",
    "Analytic",
    "Backtest",
    "BacktestError",
    "BacktestResult",
    "BrakingDrift",
    "Circuit",
    "CircuitPitLoss",
    "CompoundOffsetCalibrationResult",
    "CompoundOffsetSample",
    "Confidence",
    "Corner",
    "CrossoverKind",
    "CrossoverResult",
    "DegradationFit",
    "DriverStintState",
    "DrsZone",
    "EventSource",
    "FuelCalibrationResult",
    "FuelStintEstimate",
    "HistoricalSource",
    "KernelParams",
    "KernelReport",
    "LiveKernelSession",
    "MemorySource",
    "ParamProvenance",
    "PitLossCalibrationResult",
    "PitLossSample",
    "Reliability",
    "SessionFailure",
    "SessionFrames",
    "SessionRef",
    "StatusInterval",
    "StopPairOutcome",
    "StoreSource",
    "TrackOutline",
    "WindowState",
    "analyze_session",
    "analyze_session_from_frames",
    "annotate_laps",
    "braking_drift",
    "braking_drift_to_frame",
    "braking_points",
    "calibrate_compound_offsets",
    "calibrate_compound_offsets_from_frames",
    "calibrate_fuel_coefficient",
    "calibrate_fuel_coefficient_from_frames",
    "calibrate_pit_loss",
    "circuit_for_event",
    "clean_lap_summary",
    "clean_laps",
    "crossover_results_to_frame",
    "degradation_fits_to_frame",
    "derive_track_outline",
    "describe_params",
    "empty_frame",
    "estimate_fuel_load_kg",
    "evaluate_session_stops",
    "evaluate_session_stops_rows",
    "fit_degradation",
    "fuel_correct",
    "fuel_correction_delta_s",
    "lap_frame",
    "lap_frame_from_events",
    "list_circuits",
    "list_historical_sessions",
    "load_circuit",
    "overcut_window",
    "pit_loss_samples",
    "session_frames",
    "session_frames_from_events",
    "session_type_from_label",
    "status_during",
    "statuses_during",
    "stint_frame",
    "stint_frame_from_events",
    "summarize_degradation_by_compound",
    "telemetry_frame",
    "telemetry_frame_from_events",
    "track_status_timeline",
    "undercut_snapshot",
    "undercut_window",
]

__version__ = "0.0.1"
