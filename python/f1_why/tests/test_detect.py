"""`detect_anomalies` against the checked-in synthetic race
(`race_fixture.py`), which plants one known instance of every
`AnomalyKind` with an exact ground-truth lap/driver/direction — the same
"assert against reality, not against the kernel's own output" discipline
`f1_kernel`'s own `kernel_fixture`-based tests follow.

Magnitude assertions use a loose floor (`>=`) rather than an exact
expected value: the planted numbers are deliberately generous (a lock-up
worth 8s, a pit stop ~17s over norm, ...) so a floor well below the
planted value is still a meaningful, specific check without being brittle
to a harmless rounding change deep in `f1_kernel`'s own fitting.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from race_fixture import (
    DUEL_CHASER,
    DUEL_LEADER,
    HERO,
    PACE_LOSS_LAP,
    PACE_LOSS_SECTOR,
    PIT_ANOMALY_DRIVER,
    PIT_ANOMALY_IN_LAP,
    SESSION_KEY,
    SWAP_A,
    SWAP_B,
    SWAP_LAP,
    TOTAL_LAPS,
    build_session_frames,
)

from f1_kernel import (
    LAP_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    KernelParams,
    KernelReport,
    SessionFrames,
    analyze_session_from_frames,
    empty_frame,
)
from f1_why import ANOMALY_SCHEMA, AnomalyKind, AnomalyParams, anomalies_to_frame, detect_anomalies
from f1_why.anomaly import Anomaly

# `anomaly_params`/`anomalies` are shared fixtures from conftest.py (also
# used by test_evidence.py/test_narration.py against these SAME anomalies).


def _one(anomalies: list[Anomaly], kind: AnomalyKind) -> Anomaly:
    matches = [a for a in anomalies if a.kind is kind]
    assert len(matches) == 1, f"expected exactly one {kind.value}, got {len(matches)}: {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# each kind, at roughly the right lap
# ---------------------------------------------------------------------------


def test_pace_loss_detected_at_the_right_lap(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.PACE_LOSS)
    assert anomaly.lap_number == PACE_LOSS_LAP
    assert anomaly.drivers == (HERO,)
    assert anomaly.magnitude >= 5.0  # planted spike is 8.0s; a generous floor
    assert anomaly.summary["delta_s"] == pytest.approx(anomaly.magnitude)
    assert "slower" in anomaly.reasons[0]


def test_off_track_detected_at_the_right_lap_and_sector(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.OFF_TRACK)
    assert anomaly.lap_number == PACE_LOSS_LAP
    assert anomaly.drivers == (HERO,)
    assert anomaly.summary["sector"] == PACE_LOSS_SECTOR
    assert anomaly.magnitude >= 5.0  # planted spike is 8.0s; a generous floor


def test_position_change_detected_as_a_clean_on_track_swap(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.POSITION_CHANGE)
    assert anomaly.lap_number == SWAP_LAP
    # "5" is the gainer (P4 -> P3), "4" the loser — drivers is (gainer, loser)
    assert anomaly.drivers == (SWAP_B, SWAP_A)
    assert anomaly.magnitude == 1.0
    assert anomaly.summary["gained"] is True
    assert anomaly.summary["rival"] == SWAP_A


def test_position_change_excludes_the_duel_s_pit_stops(anomalies: list[Anomaly]) -> None:
    """Drivers "2"/"3" each pit once, which reshuffles the field around
    them — none of that may surface as POSITION_CHANGE (a pit cycle, not
    an on-track pass)."""
    position_changes = [a for a in anomalies if a.kind is AnomalyKind.POSITION_CHANGE]
    for anomaly in position_changes:
        assert DUEL_LEADER not in anomaly.drivers
        assert DUEL_CHASER not in anomaly.drivers


def test_pit_anomaly_detected_for_the_slow_stop(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.PIT_ANOMALY)
    assert anomaly.lap_number == PIT_ANOMALY_IN_LAP
    assert anomaly.drivers == (PIT_ANOMALY_DRIVER,)
    assert anomaly.magnitude >= 10.0  # planted excess is ~17.0s; a generous floor
    delta_s = anomaly.summary["delta_s"]
    assert isinstance(delta_s, int | float) and delta_s > 0  # slower than norm, not faster
    assert "slower" in anomaly.reasons[0]


def test_pit_anomaly_does_not_flag_the_duel_s_normal_cost_stops(anomalies: list[Anomaly]) -> None:
    """ "2" and "3" each pit at exactly the calibrated norm — routine
    stops, never worth a PIT_ANOMALY."""
    pit_anomalies = [a for a in anomalies if a.kind is AnomalyKind.PIT_ANOMALY]
    flagged_drivers = {driver for a in pit_anomalies for driver in a.drivers}
    assert DUEL_LEADER not in flagged_drivers
    assert DUEL_CHASER not in flagged_drivers


def test_deg_cliff_detected_for_the_hero_s_stint(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.DEG_CLIFF)
    assert anomaly.drivers == (HERO,)
    assert anomaly.lap_number == TOTAL_LAPS  # the stint's last lap
    assert anomaly.magnitude >= 5.0  # planted cliff implies ~11.5s; a generous floor
    quadratic_term = anomaly.summary["quadratic_term_s_per_lap2"]
    assert isinstance(quadratic_term, int | float) and quadratic_term > 0


def test_undercut_resolved_detected_for_the_duel(anomalies: list[Anomaly]) -> None:
    anomaly = _one(anomalies, AnomalyKind.UNDERCUT_RESOLVED)
    assert set(anomaly.drivers) == {DUEL_LEADER, DUEL_CHASER}
    assert anomaly.magnitude >= 20.0  # the hidden pace gap resolves decisively (~42s)
    assert anomaly.summary["confidence"] == "high"
    assert isinstance(anomaly.summary["hit"], bool)


# ---------------------------------------------------------------------------
# salience ordering: a race-defining moment outranks a minor wobble
# ---------------------------------------------------------------------------


def test_anomalies_are_returned_sorted_by_salience_descending(anomalies: list[Anomaly]) -> None:
    saliences = [a.salience for a in anomalies]
    assert saliences == sorted(saliences, reverse=True)


def test_salience_ordering_is_sane(anomalies: list[Anomaly]) -> None:
    """The ~42s decisive undercut resolution is the race-defining moment
    here; the single-place late-race swap is the minor wobble. The former
    must rank above the latter, and first overall."""
    undercut = _one(anomalies, AnomalyKind.UNDERCUT_RESOLVED)
    position_change = _one(anomalies, AnomalyKind.POSITION_CHANGE)
    assert undercut.salience > position_change.salience
    assert undercut is anomalies[0]


def test_salience_is_always_in_the_zero_one_range(anomalies: list[Anomaly]) -> None:
    for anomaly in anomalies:
        assert 0.0 <= anomaly.salience < 1.0


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_detect_anomalies_is_deterministic(
    race_frames: SessionFrames, race_report: KernelReport, anomaly_params: AnomalyParams
) -> None:
    first = detect_anomalies(race_frames, race_report, anomaly_params)
    second = detect_anomalies(race_frames, race_report, anomaly_params)
    assert first == second


def test_detect_anomalies_is_deterministic_across_fresh_frames(
    race_report: KernelReport, anomaly_params: AnomalyParams
) -> None:
    """Rebuilding the frames from scratch (a fresh `SessionFrames`, not the
    session-scoped fixture instance) must give byte-identical results —
    the same guarantee `f1_kernel.analyze_session`'s own determinism test
    makes for the kernel itself."""
    rebuilt_frames = build_session_frames()
    rebuilt_report = analyze_session_from_frames(rebuilt_frames, KernelParams())
    first = detect_anomalies(rebuilt_frames, rebuilt_report, anomaly_params)
    second = detect_anomalies(rebuilt_frames, race_report, anomaly_params)
    assert first == second


# ---------------------------------------------------------------------------
# graceful degradation
# ---------------------------------------------------------------------------


def test_empty_session_yields_no_anomalies_instead_of_crashing(kernel_params: KernelParams) -> None:
    empty = SessionFrames(
        session_key=SESSION_KEY,
        laps=empty_frame(LAP_FRAME_SCHEMA),
        stints=empty_frame(STINT_FRAME_SCHEMA),
    )
    report = analyze_session_from_frames(empty, kernel_params)

    assert detect_anomalies(empty, report, AnomalyParams()) == []


# ---------------------------------------------------------------------------
# anomalies_to_frame
# ---------------------------------------------------------------------------


def test_anomalies_to_frame_round_trips_every_row(anomalies: list[Anomaly]) -> None:
    frame = anomalies_to_frame(anomalies)
    assert frame.height == len(anomalies)
    assert set(frame["kind"].to_list()) == {a.kind.value for a in anomalies}


def test_anomalies_to_frame_empty_list_has_the_right_schema() -> None:
    frame = anomalies_to_frame([])
    assert frame.is_empty()
    assert dict(frame.schema) == ANOMALY_SCHEMA


# ---------------------------------------------------------------------------
# AnomalyParams
# ---------------------------------------------------------------------------


def test_params_with_overrides_applies_and_validates() -> None:
    params = AnomalyParams().with_overrides(salience_scale_s=10.0)
    assert params.salience_scale_s == 10.0

    with pytest.raises(ValidationError):
        AnomalyParams().with_overrides(not_a_real_field=1)


def test_params_round_trip_through_json() -> None:
    params = AnomalyParams()
    assert AnomalyParams.model_validate_json(params.model_dump_json()) == params
