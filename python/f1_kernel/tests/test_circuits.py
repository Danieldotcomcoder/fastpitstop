"""Circuit metadata: the shipped files load, and say honestly where their
numbers came from."""

from __future__ import annotations

import pytest

from f1_kernel import Circuit, circuit_for_event, list_circuits, load_circuit
from f1_kernel.circuit import CIRCUITS_DIR


def test_ships_circuits() -> None:
    circuits = list_circuits()

    assert len(circuits) >= 5
    assert "bahrain" in circuits
    assert circuits == sorted(circuits)


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_every_shipped_circuit_is_valid(circuit_id: str) -> None:
    """Every file parses into the model — a malformed one must fail here,
    not inside an analytic."""
    circuit = load_circuit(circuit_id)

    assert isinstance(circuit, Circuit)
    assert circuit.circuit_id == circuit_id
    assert circuit.lap_distance_m > 1000
    assert circuit.pit_loss_s > 0
    assert circuit.event_names


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_corner_distances_are_ordered_and_on_track(circuit_id: str) -> None:
    circuit = load_circuit(circuit_id)

    distances = [corner.distance_m for corner in circuit.corners]
    assert distances, f"{circuit_id} has no corners"
    assert distances == sorted(distances), f"{circuit_id} corners are out of order"
    assert max(distances) <= circuit.lap_distance_m
    numbers = [corner.number for corner in circuit.corners]
    assert numbers == sorted(numbers)


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_drs_zones_are_sane(circuit_id: str) -> None:
    circuit = load_circuit(circuit_id)

    for zone in circuit.drs_zones:
        assert zone.end_m <= circuit.lap_distance_m
        if zone.wraps_start_finish:
            # A wrapping zone runs from start_m to the line and on to end_m.
            assert zone.start_m > zone.end_m
            assert zone.length_m(circuit.lap_distance_m) > 0
        else:
            assert zone.end_m > zone.start_m
            assert zone.length_m() > 0


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_pit_loss_declares_its_provenance_honestly(circuit_id: str) -> None:
    """Pit loss is either CALIBRATED from our own measured stops — in which
    case the file must carry the evidence (uncertainty, stop count) — or
    still provisional, in which case it must say so and carry NO invented
    uncertainty. See docs/PHASE2.md 'Pit-loss calibration'."""
    circuit = load_circuit(circuit_id)

    assert circuit.pit_loss_note
    if circuit.pit_loss_provenance == "calibrated":
        assert circuit.pit_loss_uncertainty_s is not None
        assert circuit.pit_loss_uncertainty_s > 0
        assert circuit.pit_loss_n_stops is not None
        assert circuit.pit_loss_n_stops >= 8, "calibrated on fewer stops than the floor"
    else:
        assert circuit.pit_loss_provenance == "provisional"
        assert circuit.pit_loss_uncertainty_s is None
        assert circuit.pit_loss_n_stops is None


def test_some_shipped_circuits_are_calibrated() -> None:
    """The 2024 backfill calibrated four of the six shipped files; if a
    regeneration quietly dropped them all back to provisional, that is a
    regression, not housekeeping."""
    provenances = {cid: load_circuit(cid).pit_loss_provenance for cid in list_circuits()}

    assert sum(1 for p in provenances.values() if p == "calibrated") >= 3


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_geometry_records_where_it_came_from(circuit_id: str) -> None:
    """Corners and DRS zones are derived from a real session; the file says
    which one, so a value can always be traced."""
    circuit = load_circuit(circuit_id)

    assert "fastf1" in circuit.geometry_source


def test_known_circuit_geometry_matches_reality() -> None:
    """Spot-check against facts about these circuits that are not in
    dispute: corner counts and DRS zone counts. If the derivation drifts,
    this is what catches it."""
    expected_corners = {
        "bahrain": 15,
        "jeddah": 27,
        "albert_park": 14,
        "suzuka": 18,
        "monaco": 19,
        "monza": 11,
    }
    expected_zones = {
        "bahrain": 3,
        "jeddah": 3,
        "albert_park": 4,
        "suzuka": 1,
        "monaco": 1,
        "monza": 2,
    }

    for circuit_id, corners in expected_corners.items():
        circuit = load_circuit(circuit_id)
        assert len(circuit.corners) == corners, circuit_id
        assert len(circuit.drs_zones) == expected_zones[circuit_id], circuit_id


def test_lookup_by_event_name() -> None:
    assert circuit_for_event("Bahrain Grand Prix") is not None
    assert circuit_for_event("bahrain grand prix ") is not None
    assert circuit_for_event("Bahrain Grand Prix").circuit_id == "bahrain"  # type: ignore[union-attr]


def test_lookup_of_an_unshipped_circuit_returns_none() -> None:
    """We ship six of twenty-four venues. An analytic must degrade to the
    default pit loss, not crash."""
    assert circuit_for_event("Belgian Grand Prix") is None
    assert circuit_for_event(None) is None
    assert circuit_for_event("") is None


def test_loading_an_unknown_circuit_raises_with_a_useful_message() -> None:
    with pytest.raises(KeyError, match="shipped circuits"):
        load_circuit("nurburgring")


def test_corner_and_zone_lookup_by_distance() -> None:
    circuit = load_circuit("bahrain")
    first_corner = circuit.corners[0]

    assert circuit.corner_at(0.0) == first_corner
    assert circuit.corner_at(first_corner.distance_m) == first_corner
    assert circuit.corner_at(circuit.lap_distance_m) is None

    zone = circuit.drs_zones[0]
    midpoint = (
        (zone.start_m + zone.end_m) / 2
        if not zone.wraps_start_finish
        else max(zone.start_m, 0.0) + 1.0
    )
    assert circuit.in_drs_zone(midpoint) is not None


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_shipped_circuits_have_a_real_outline(circuit_id: str) -> None:
    """Every shipped circuit has been through `tools/build_circuits.py`'s
    outline step against the populated FastF1 cache (docs/PHASE3.md's Track
    map section) — the map must have real geometry to draw, not the
    graceful-degradation `None` state, for every circuit this repo ships."""
    circuit = load_circuit(circuit_id)

    assert circuit.outline is not None
    assert len(circuit.outline.points) > 2
    assert circuit.outline.width_m > 0
    assert circuit.outline.height_m > 0
    assert circuit.outline.source_points > 0


def test_circuit_outline_defaults_to_none_when_absent() -> None:
    """The graceful-degradation path itself (Phase 3.4: "Phase 3 must render
    gracefully without one") — exercised directly on `Circuit` rather than
    via a shipped file, now that every shipped circuit has a real outline."""
    circuit = Circuit(
        circuit_id="test_circuit",
        name="Test Circuit",
        country="Testland",
        lap_distance_m=1000.0,
        pit_loss_s=20.0,
    )

    assert circuit.outline is None


def test_circuit_files_live_in_the_package() -> None:
    """They must ship inside the wheel, not sit beside it, or an installed
    kernel has no circuit data at all."""
    assert CIRCUITS_DIR.is_dir()
    assert (CIRCUITS_DIR / "bahrain.json").is_file()
    assert CIRCUITS_DIR.parent.name == "f1_kernel"
