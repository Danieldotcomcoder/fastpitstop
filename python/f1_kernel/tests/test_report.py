"""`analyze_session` / `KernelReport` / `LiveKernelSession` — Prompt 2.7's
unified kernel API, checked against the same checked-in fixture (and its
ground truth) every other Phase 2 suite uses.

The point of these tests is less "does each stage work" (every stage
already has its own suite) and more "does the WIRING hold": does the report
carry every stage's output with the right shape, does an empty/thin session
degrade instead of crashing, and does the live variant's accumulated state
converge on exactly what the batch loader produces from the same events.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest
from kernel_fixture import GREEN_RACE, SC_RACE, load_truth, truth_key

from f1_domain import DomainEvent, RaceControlMessage, SessionKey, SessionType
from f1_kernel import (
    ANNOTATED_LAP_FRAME_SCHEMA,
    LAP_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    HistoricalSource,
    KernelParams,
    KernelReport,
    LiveKernelSession,
    Reliability,
    analyze_session,
    analyze_session_from_frames,
    empty_frame,
)
from f1_kernel.frames import SessionFrames
from f1_kernel.sources import LAP_FRAME_KINDS


@pytest.fixture
def sc_report(source: HistoricalSource, params: KernelParams) -> KernelReport:
    return analyze_session(source, SC_RACE, params)


@pytest.fixture
def green_report(source: HistoricalSource, params: KernelParams) -> KernelReport:
    return analyze_session(source, GREEN_RACE, params)


# ---------------------------------------------------------------------------
# the wiring: every stage present, in the documented order
# ---------------------------------------------------------------------------


def test_runs_end_to_end_with_no_crash(sc_report: KernelReport, green_report: KernelReport) -> None:
    for report in (sc_report, green_report):
        assert not report.frames.laps.is_empty()
        assert not report.clean_laps.is_empty()
        assert not report.fuel_corrected_laps.is_empty()
        assert report.degradation_fits
        assert report.crossovers
        assert report.notes == ()


def test_identity_and_params_are_carried_through(
    sc_report: KernelReport, params: KernelParams
) -> None:
    assert sc_report.session_key == SC_RACE
    assert sc_report.event_name == "Bahrain Grand Prix"
    assert sc_report.params == params
    assert sc_report.frames.circuit is not None
    assert sc_report.frames.circuit.circuit_id == "bahrain"


def test_clean_laps_matches_the_annotate_then_filter_contract(sc_report: KernelReport) -> None:
    assert dict(sc_report.annotated_laps.schema) == ANNOTATED_LAP_FRAME_SCHEMA
    assert sc_report.clean_laps.equals(sc_report.annotated_laps.filter(pl.col("is_clean")))
    assert sc_report.clean_laps.height < sc_report.frames.laps.height


def test_lap_summary_matches_clean_lap_counts(sc_report: KernelReport) -> None:
    assert sc_report.lap_summary["clean_laps"].sum() == sc_report.clean_laps.height


def test_fuel_corrected_laps_adds_the_fuel_columns(sc_report: KernelReport) -> None:
    assert {"fuel_load_kg", "lap_time_fuel_corrected_s"} <= set(
        sc_report.fuel_corrected_laps.columns
    )
    assert sc_report.fuel_corrected_laps.height == sc_report.clean_laps.height


def test_degradation_fits_and_summary_agree(sc_report: KernelReport) -> None:
    assert len(sc_report.degradation_fits) == sc_report.degradation_by_compound["n_fits"].sum()
    for fit in sc_report.degradation_fits:
        assert fit.reliability in Reliability


def test_total_laps_resolves_from_the_frame(
    sc_report: KernelReport, green_report: KernelReport
) -> None:
    truth = load_truth()
    assert sc_report.total_laps == truth[truth_key(SC_RACE)]["total_laps"]
    assert green_report.total_laps == truth[truth_key(GREEN_RACE)]["total_laps"]


def test_total_laps_can_be_overridden(source: HistoricalSource, params: KernelParams) -> None:
    report = analyze_session(source, SC_RACE, params, total_laps=20)
    assert report.total_laps == 20


def test_crossovers_default_to_the_latest_common_lap(sc_report: KernelReport) -> None:
    truth = load_truth()
    assert sc_report.decision_lap == truth[truth_key(SC_RACE)]["total_laps"]
    for crossover in sc_report.crossovers:
        assert crossover.decision_lap == sc_report.decision_lap
        assert crossover.attacker != crossover.defender


def test_crossovers_honour_an_explicit_decision_lap(
    source: HistoricalSource, params: KernelParams
) -> None:
    report = analyze_session(source, SC_RACE, params, decision_lap=10)

    assert report.decision_lap == 10
    assert report.crossovers
    for crossover in report.crossovers:
        assert crossover.decision_lap == 10


# ---------------------------------------------------------------------------
# braking-point drift: opt-in, via the source-loading wrapper
# ---------------------------------------------------------------------------


def test_braking_is_skipped_by_default(sc_report: KernelReport) -> None:
    assert sc_report.braking_drift == ()
    assert sc_report.braking_drivers == ()


def test_braking_runs_when_a_driver_opts_in(source: HistoricalSource, params: KernelParams) -> None:
    """The fixture ships telemetry for driver '1' only (SC_RACE) — see
    `fixtures/generate.py`'s `telemetry_driver`."""
    report = analyze_session(source, SC_RACE, params, braking_drivers=["1"])

    assert report.braking_drivers == ("1",)
    assert len(report.braking_drift) == 1
    fit = report.braking_drift[0]
    assert fit.driver == "1"
    assert fit.reliability in Reliability


def test_braking_note_when_telemetry_has_no_circuit(params: KernelParams) -> None:
    """A driver's telemetry with no shipped circuit metadata degrades to an
    explicit note, not a crash. `frames.circuit is None` short-circuits
    before `braking_points` ever sees the (deliberately malformed) frame."""
    frames = SessionFrames(
        session_key=SC_RACE,
        laps=empty_frame(LAP_FRAME_SCHEMA),
        stints=empty_frame(STINT_FRAME_SCHEMA),
        event_name=None,
    )
    telemetry = {"1": pl.DataFrame()}

    report = analyze_session_from_frames(frames, params, telemetry=telemetry)

    assert report.braking_drift == ()
    assert any("no shipped circuit metadata" in note for note in report.notes)


# ---------------------------------------------------------------------------
# graceful degradation on thin/empty input
# ---------------------------------------------------------------------------


def test_an_empty_session_degrades_instead_of_crashing(params: KernelParams) -> None:
    empty = SessionFrames(
        session_key=SC_RACE,
        laps=empty_frame(LAP_FRAME_SCHEMA),
        stints=empty_frame(STINT_FRAME_SCHEMA),
        event_name=None,
    )

    report = analyze_session_from_frames(empty, params)

    assert report.frames.laps.is_empty()
    assert report.clean_laps.is_empty()
    assert report.degradation_fits == ()
    assert report.crossovers == ()
    assert report.total_laps is None
    assert report.decision_lap is None
    assert "no laps in frames" in report.notes[0]


def test_summary_is_a_readable_string(sc_report: KernelReport) -> None:
    text = sc_report.summary()

    assert "2024 R01 race" in text
    assert "Bahrain Grand Prix" in text
    assert "degradation fits" in text


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_analyze_session_is_deterministic(source: HistoricalSource, params: KernelParams) -> None:
    first = analyze_session(source, SC_RACE, params)
    second = analyze_session(source, SC_RACE, params)

    assert first.annotated_laps.equals(second.annotated_laps)
    assert first.fuel_corrected_laps.equals(second.fuel_corrected_laps)
    assert first.degradation_fits == second.degradation_fits
    assert first.crossovers == second.crossovers


# ---------------------------------------------------------------------------
# the live variant
# ---------------------------------------------------------------------------


def _events_for(source: HistoricalSource, key: SessionKey) -> list[DomainEvent]:
    return list(source.events(key, kinds=LAP_FRAME_KINDS))


def test_live_session_converges_on_the_batch_report(
    source: HistoricalSource, params: KernelParams, sc_report: KernelReport
) -> None:
    events = _events_for(source, SC_RACE)
    half = len(events) // 2
    live = LiveKernelSession(session_key=SC_RACE, params=params, event_name="Bahrain Grand Prix")

    first = live.push(events[:half])
    final = live.push(events[half:])

    # Progress is real between pushes...
    assert first.frames.laps.height < final.frames.laps.height
    # ...and the fully-accumulated report matches a straight batch load.
    assert final.frames.laps.equals(sc_report.frames.laps)
    assert final.degradation_fits == sc_report.degradation_fits
    assert len(live.events) == len(events)


def test_live_session_ignores_events_for_another_session(params: KernelParams) -> None:
    other_key = SessionKey(year=1999, round=1, session_type=SessionType.RACE)
    live = LiveKernelSession(session_key=SC_RACE, params=params)
    foreign = RaceControlMessage(
        session_key=other_key,
        ts_utc=datetime(2024, 1, 1, tzinfo=UTC),
        source_seq=0,
        message="irrelevant",
    )

    report = live.push([foreign])

    assert live.events == ()
    assert report.frames.laps.is_empty()
