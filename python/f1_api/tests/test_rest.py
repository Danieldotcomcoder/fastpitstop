"""Every REST endpoint returns valid data for a checked-in historical
session — the Phase 3.1 test requirement from CLAUDE.md's prompt."""

from __future__ import annotations

import pytest
from api_fixture import GREEN_SESSION_KEY, SESSION_KEY
from fastapi.testclient import TestClient

from f1_api.schemas import (
    BrakingPointRowOut,
    CircuitOut,
    DegradationFitOut,
    KernelReportOut,
    LapRowOut,
    SessionDetailOut,
    SessionSummaryOut,
)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_sessions(client: TestClient) -> None:
    response = client.get("/sessions")
    assert response.status_code == 200
    sessions = [SessionSummaryOut.model_validate(row) for row in response.json()]
    keys = {s.key for s in sessions}
    assert {SESSION_KEY, GREEN_SESSION_KEY} <= keys
    assert all(s.provenance == "historical" for s in sessions)


def test_session_detail(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}")
    assert response.status_code == 200
    detail = SessionDetailOut.model_validate(response.json())
    assert detail.key == SESSION_KEY
    assert detail.event_name == "Bahrain Grand Prix"
    assert detail.provenance == "historical"
    assert detail.track_status is not None  # the fixture has a safety car period
    assert detail.classification
    positions = [row.position for row in detail.classification if row.position is not None]
    assert positions == sorted(positions)
    assert positions[0] == 1


def test_session_not_found_is_404(client: TestClient) -> None:
    response = client.get("/sessions/2099-1-race")
    assert response.status_code == 404


def test_malformed_session_key_is_422(client: TestClient) -> None:
    response = client.get("/sessions/not-a-valid-key")
    assert response.status_code == 422


def test_laps(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}/laps")
    assert response.status_code == 200
    rows = [LapRowOut.model_validate(row) for row in response.json()]
    assert rows
    # the clean-lap filter's discipline (docs/PHASE2.md): every lap is
    # present, clean or not, with its own exclusion reasons.
    assert any(row.is_sc for row in rows), "fixture race has a safety-car period"
    assert any(row.is_clean for row in rows)
    assert any(not row.is_clean for row in rows)
    for row in rows:
        assert row.is_clean == (not any(row.exclusion_reasons))
    # The kernel's own fuel-corrected time rides along, in the units the
    # degradation fits use, so a chart can put points and fit on one axis.
    timed = [row for row in rows if row.lap_time_s is not None]
    assert timed and all(row.lap_time_fuel_corrected_s is not None for row in timed)
    assert any(row.lap_time_fuel_corrected_s != row.lap_time_s for row in timed)


def test_degradation(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}/degradation")
    assert response.status_code == 200
    fits = [DegradationFitOut.model_validate(row) for row in response.json()]
    assert fits
    assert {fit.reliability for fit in fits} <= {"good", "thin", "unreliable"}


def test_analysis(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}/analysis")
    assert response.status_code == 200
    report = KernelReportOut.model_validate(response.json())
    assert report.event_name == "Bahrain Grand Prix"
    assert report.degradation_fits
    assert report.crossovers
    assert report.lap_summary
    assert report.braking_drift == []  # opt-in; not requested here
    assert report.braking_drivers == []
    assert report.summary  # a non-empty human-readable recap


def test_analysis_opts_into_braking_drift(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}/analysis", params={"braking_drivers": ["1"]})
    assert response.status_code == 200
    report = KernelReportOut.model_validate(response.json())
    # Whether driver 1's stints clear the min-laps floor is incidental to
    # this test; what matters is opting in never crashes and only ever
    # reports drivers actually asked for.
    assert set(report.braking_drivers) <= {"1"}


def test_telemetry_braking_points(client: TestClient) -> None:
    response = client.get(f"/sessions/{SESSION_KEY}/telemetry/1")
    assert response.status_code == 200
    rows = [BrakingPointRowOut.model_validate(row) for row in response.json()]
    assert rows, "round 1 is the fixture race with a car_telemetry table"
    assert all(row.driver == "1" for row in rows)


def test_telemetry_unknown_driver_degrades_to_empty_list(client: TestClient) -> None:
    """`braking_points` degrades gracefully rather than raising (its own
    documented discipline) — the route must not turn "no data" into a 404."""
    response = client.get(f"/sessions/{SESSION_KEY}/telemetry/not-a-real-driver")
    assert response.status_code == 200
    assert response.json() == []


def test_circuit_for_a_shipped_venue(client: TestClient) -> None:
    """The fixture race's event name ("Bahrain Grand Prix") matches a
    shipped circuit file, so corners/DRS zones come back real. The outline
    is real too now — `tools/build_circuits.py`'s outline step has been run
    against the populated FastF1 cache for every shipped circuit
    (docs/PHASE2.md's circuit-metadata section) — this is no longer the
    PROVISIONAL-by-omission gap it used to be."""
    response = client.get(f"/sessions/{SESSION_KEY}/circuit")
    assert response.status_code == 200
    circuit = CircuitOut.model_validate(response.json())
    assert circuit.circuit_id == "bahrain"
    assert circuit.corners
    assert circuit.drs_zones
    assert circuit.outline is not None
    assert len(circuit.outline.points) > 2
    # The pit lane rides along, from the reference race's real stops.
    assert circuit.pit_lane is not None
    assert circuit.pit_lane.source_stops >= 3
    assert 200 < circuit.pit_lane.length_m < 700


def test_circuit_for_the_other_fixture_race_is_also_shipped(client: TestClient) -> None:
    """The green-flag fixture race ("Saudi Arabian Grand Prix") also happens
    to match a shipped circuit (Jeddah) — both fixture races do, since only
    six of twenty-four venues ship metadata at all."""
    response = client.get(f"/sessions/{GREEN_SESSION_KEY}/circuit")
    assert response.status_code == 200
    circuit = CircuitOut.model_validate(response.json())
    assert circuit.circuit_id == "jeddah"


def test_circuit_for_an_unshipped_venue_degrades_to_null_not_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither fixture race's event is missing a shipped circuit, so this
    exercises the degrade-gracefully path directly at its actual seam
    rather than skipping it: `None`, not a 404, is the contract for a
    session that exists but has no shipped circuit — the same rule
    `f1_kernel.circuit_for_event` itself documents. Every session's circuit
    is resolved in one place, `frames.session_frames_from_events` (by feed
    venue, then Grand Prix name), so that is the one seam to make miss."""

    def _no_circuit(event_name: str | None, feed_circuit_name: str | None = None) -> None:
        return None

    monkeypatch.setattr("f1_kernel.frames.circuit_for_session", _no_circuit)

    response = client.get(f"/sessions/{SESSION_KEY}/circuit")

    assert response.status_code == 200
    assert response.json() is None


def test_circuit_for_an_unknown_session_is_404(client: TestClient) -> None:
    response = client.get("/sessions/2099-1-race/circuit")
    assert response.status_code == 404


def test_green_race_has_no_safety_car_laps(client: TestClient) -> None:
    response = client.get(f"/sessions/{GREEN_SESSION_KEY}/laps")
    assert response.status_code == 200
    rows = [LapRowOut.model_validate(row) for row in response.json()]
    assert rows
    assert not any(row.is_sc for row in rows)
