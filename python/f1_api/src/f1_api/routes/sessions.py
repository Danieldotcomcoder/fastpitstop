"""The REST surface: `GET /sessions*`. Every handler resolves a session via
`f1_api.sessions.resolve_session`, loads the frames it needs, and hands them
to the kernel — no analytic is reimplemented here (CLAUDE.md's Phase 3
invariant).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from f1_api.calls import enabled_call_kinds, session_call_ledger
from f1_api.config import Settings, get_settings
from f1_api.convert import (
    braking_point_rows,
    circuit_out,
    degradation_fit_rows,
    kernel_report_out,
    lap_rows,
    session_detail,
    session_summary,
)
from f1_api.geometry import session_positions
from f1_api.response_cache import ResponseCache
from f1_api.schemas import (
    BrakingPointRowOut,
    CallLedgerOut,
    CircuitOut,
    DegradationFitOut,
    KernelReportOut,
    LapRowOut,
    SessionDetailOut,
    SessionSummaryOut,
)
from f1_api.sessions import ResolvedSession, list_sessions, parse_session_key, resolve_session
from f1_api.sessions import session_key_path as _session_key_path
from f1_domain import SessionKey
from f1_kernel import (
    KernelParams,
    KernelReport,
    analyze_session,
    annotate_laps,
    braking_points,
    fuel_correct,
    lap_frame,
    race_distance_laps,
    session_frames,
    telemetry_frame,
)

router = APIRouter(tags=["sessions"])

SESSION_CACHE_SIZE = 32
"""Finished sessions whose laps and circuit are kept per process (see
`f1_api.response_cache`). A race's annotated laps are well under a
megabyte."""
LIVE_CACHE_TTL_S = 15.0
"""How long a running session's laps and circuit are reused: a viewer
reads `/laps` once, when the desk opens, so this only merges the viewers
who open it within the same few seconds."""

laps_cache: ResponseCache[list[LapRowOut]] = ResponseCache(SESSION_CACHE_SIZE, LIVE_CACHE_TTL_S)
circuit_cache: ResponseCache[CircuitOut | None] = ResponseCache(
    SESSION_CACHE_SIZE, LIVE_CACHE_TTL_S
)


def _cache_key(settings: Settings, resolved: ResolvedSession) -> tuple[str, str | None, str, str]:
    """Where the answer came from, as well as which session: the same key
    can be served by the store or the backfill, and tests point each app at
    its own fixture."""
    return (
        str(settings.historical_root),
        settings.store_dsn,
        _session_key_path(resolved.entry.session_key),
        resolved.entry.provenance,
    )


def get_session_key(key: str) -> SessionKey:
    """Path-param dependency: turns the `{key}` segment into a `SessionKey`,
    422ing on anything malformed rather than letting a `ValueError` become
    a 500."""
    try:
        return parse_session_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


SessionKeyDep = Annotated[SessionKey, Depends(get_session_key)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _not_found(session_key: SessionKey) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"no session {_session_key_path(session_key)} in the historical backfill "
        "or (if configured) the live store",
    )


def _analyze(
    resolved: ResolvedSession,
    session_key: SessionKey,
    *,
    braking_drivers: list[str] | None = None,
) -> KernelReport:
    return analyze_session(
        resolved.source,
        session_key,
        KernelParams(),
        event_name=resolved.entry.event_name,
        braking_drivers=braking_drivers,
    )


@router.get("/sessions", response_model=list[SessionSummaryOut])
def get_sessions(settings: SettingsDep) -> list[SessionSummaryOut]:
    """Every session available to analyze, live + historical."""
    return [session_summary(entry) for entry in list_sessions(settings)]


@router.get("/sessions/{key}", response_model=SessionDetailOut)
def get_session(session_key: SessionKeyDep, settings: SettingsDep) -> SessionDetailOut:
    """Session metadata plus classification (final for a finished
    historical session, current running order for a live one)."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        laps = lap_frame(resolved.source, session_key)
        status_events = list(
            resolved.source.events(
                session_key,
                kinds=[
                    "session_status_changed",
                    "track_status_changed",
                    "race_control_message",
                    "driver_list_updated",
                ],
            )
        )
        return session_detail(resolved.entry, laps, status_events)


@router.get("/sessions/{key}/laps", response_model=list[LapRowOut])
def get_laps(session_key: SessionKeyDep, settings: SettingsDep) -> list[LapRowOut]:
    """The annotated lap frame: every lap, clean or not, with its exclusion
    reasons (`f1_kernel.annotate_laps`) — nothing is silently dropped."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        source = resolved.source

        def compute() -> list[LapRowOut]:
            laps = lap_frame(source, session_key)
            params = KernelParams()
            annotated = annotate_laps(laps, params)
            # The race distance the live kernel uses too (`race_distance_laps`):
            # the feed's own lap counter when there is one, else the furthest lap.
            distance = race_distance_laps(
                source.events(session_key, kinds=["lap_counter_updated", "lap_completed"])
            )
            return lap_rows(fuel_correct(annotated, params, total_laps=distance))

        return laps_cache.get(
            _cache_key(settings, resolved), live=resolved.entry.live, compute=compute
        )


@router.get("/sessions/{key}/degradation", response_model=list[DegradationFitOut])
def get_degradation(session_key: SessionKeyDep, settings: SettingsDep) -> list[DegradationFitOut]:
    """`DegradationFit[]` with reliability signals — one per (driver, stint,
    compound). Runs the same `analyze_session` chain `/analysis` does, so
    the two never disagree about a fit."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        report = _analyze(resolved, session_key)
        return degradation_fit_rows(report.degradation_fits)


@router.get("/sessions/{key}/analysis", response_model=KernelReportOut)
def get_analysis(
    session_key: SessionKeyDep,
    settings: SettingsDep,
    braking_drivers: Annotated[
        list[str] | None,
        Query(description="Opt specific drivers into the braking-point-drift stage (expensive)."),
    ] = None,
) -> KernelReportOut:
    """The full `KernelReport`: clean -> fuel-correct -> degradation ->
    crossover -> (opt-in) braking-drift, in that order."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        report = _analyze(resolved, session_key, braking_drivers=braking_drivers)
        return kernel_report_out(report, resolved.entry)


@router.get("/sessions/{key}/calls", response_model=CallLedgerOut)
def get_calls(
    session_key: SessionKeyDep,
    settings: SettingsDep,
    upto: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "The lap the viewer has watched to: nothing past its end is in the "
                "answer. Omit for the whole race."
            ),
        ),
    ] = None,
) -> CallLedgerOut:
    """The calls ledger (ROADMAP W5): every undercut and rejoin call locked
    by the end of `upto`, how each resolved, and the scorecard. Only the call
    kinds a passing held-out backtest cleared are served."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        source = resolved.source
        frames = session_frames(source, session_key, event_name=resolved.entry.event_name)
        distance = race_distance_laps(
            source.events(session_key, kinds=["lap_counter_updated", "lap_completed"])
        )
        return session_call_ledger(
            frames,
            KernelParams(),
            enabled_call_kinds(settings),
            upto_lap=upto,
            total_laps=distance,
            running=resolved.entry.live,
        )


@router.get("/sessions/{key}/circuit", response_model=CircuitOut | None)
def get_circuit(session_key: SessionKeyDep, settings: SettingsDep) -> CircuitOut | None:
    """The circuit geometry the track map (Phase 3.4) renders: corners, DRS
    zones and (once `tools/build_circuits.py`'s outline step has run for
    this venue) the real 2D lap shape. `None`, not a 404, when the
    session's event does not match one of the few circuits this project
    ships metadata for yet — the same degrade-gracefully rule
    `f1_kernel.circuit_for_event` already follows."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        source = resolved.source
        event_name = resolved.entry.event_name

        def compute() -> CircuitOut | None:
            frames = session_frames(source, session_key, event_name=event_name)
            if frames.circuit is None:
                return None
            # The same geometry the stream draws its dots against: the circuit
            # file's lines, or this session's own when those do not fit it
            # (f1_api.geometry).
            geometry, _ = session_positions(settings, source, session_key, frames.circuit)
            return circuit_out(frames.circuit, geometry)

        return circuit_cache.get(
            _cache_key(settings, resolved), live=resolved.entry.live, compute=compute
        )


@router.get("/sessions/{key}/telemetry/{driver}", response_model=list[BrakingPointRowOut])
def get_telemetry(
    session_key: SessionKeyDep, driver: str, settings: SettingsDep
) -> list[BrakingPointRowOut]:
    """The braking-point frame for one driver (`f1_kernel.braking_points`):
    one row per (lap, corner) sustained braking-application distance — the
    novel dataset Phase 2.6 built instead of needing computer vision.
    Degrades to an empty list rather than 404ing on a driver/session with
    too little telemetry, matching `braking_points`' own discipline."""
    with resolve_session(settings, session_key) as resolved:
        if resolved is None:
            raise _not_found(session_key)
        frames = session_frames(resolved.source, session_key, event_name=resolved.entry.event_name)
        telemetry = telemetry_frame(resolved.source, session_key, driver)
        points = braking_points(telemetry, frames.circuit, KernelParams())
        return braking_point_rows(points)


__all__ = ["router"]
