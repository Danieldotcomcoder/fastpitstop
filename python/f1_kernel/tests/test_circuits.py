"""Circuit metadata: the shipped files load, and say honestly where their
numbers came from."""

from __future__ import annotations

import pytest

from f1_kernel import Circuit, circuit_for_event, circuit_for_session, list_circuits, load_circuit
from f1_kernel.circuit import CIRCUITS_DIR, circuit_for_schedule


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
    # Findable somehow: by Grand Prix name, or by the feed's venue name
    # alone (Madring, whose Grand Prix name belongs to Barcelona's history).
    assert circuit.event_names or circuit.feed_circuit_names


CORNERLESS = {"madring", "sepang"}
"""Venues shipped deliberately without corners: FastF1's circuit-info
source had none for a new (or returning, 2026 Sepang) venue, and corners are never guessed. Its
map still works (outline and lap distance come from telemetry); braking
analysis, which is keyed by corner, simply has nothing to report there."""


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_corner_distances_are_ordered_and_on_track(circuit_id: str) -> None:
    circuit = load_circuit(circuit_id)

    distances = [corner.distance_m for corner in circuit.corners]
    if circuit_id in CORNERLESS:
        assert distances == [], f"{circuit_id} gained corners; drop it from CORNERLESS"
        return
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
    """Every current venue ships, but a race at one that does not (a new
    venue before its first build) must degrade to the default pit loss and
    no map, not crash."""
    assert circuit_for_event("Portuguese Grand Prix") is None
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


def test_every_venue_on_the_calendar_ships() -> None:
    """The track map is drawn from these files, so a missing venue is a
    race with no map. All 24 venues of the 2024 calendar ship, plus the two
    2026 additions (Madring, and Sepang for that season's Bahrain GP)."""
    assert len([c for c in list_circuits() if c not in {"madring", "sepang"}]) == 24


@pytest.mark.parametrize("circuit_id", list_circuits())
def test_every_circuit_knows_its_feed_venue_name(circuit_id: str) -> None:
    """How a live session finds its circuit: the feed sends the venue's
    short name, never a circuit id."""
    assert load_circuit(circuit_id).feed_circuit_names


def test_no_two_circuits_answer_to_the_same_name() -> None:
    """A shared name is a race drawn on the wrong track. The "Spanish Grand
    Prix" moved from Barcelona to Madrid in 2026; only one file may claim
    it, and the other is found by its feed venue name instead."""
    seen_events: dict[str, str] = {}
    seen_venues: dict[str, str] = {}
    for circuit_id in list_circuits():
        circuit = load_circuit(circuit_id)
        for name in circuit.event_names:
            key = name.strip().casefold()
            assert key not in seen_events, f"{name!r}: {seen_events.get(key)} and {circuit_id}"
            seen_events[key] = circuit_id
        for name in circuit.feed_circuit_names:
            key = name.strip().casefold()
            assert key not in seen_venues, f"{name!r}: {seen_venues.get(key)} and {circuit_id}"
            seen_venues[key] = circuit_id


def test_the_feed_venue_wins_over_the_grand_prix_name() -> None:
    """A live session names its venue; a Grand Prix name is only the
    fallback, for historical sessions that carry nothing else."""
    venue = circuit_for_session("Bahrain Grand Prix", "Monte Carlo")
    assert venue is not None and venue.circuit_id == "monaco"


def test_the_grand_prix_name_is_the_fallback() -> None:
    by_name = circuit_for_session("Spanish Grand Prix", None)
    assert by_name is not None and by_name.circuit_id == "barcelona"
    assert circuit_for_session(None, None) is None


def test_an_unknown_venue_is_never_drawn_on_another_track() -> None:
    """The regression: the 2026 "Bahrain Grand Prix" ran at Sepang, which
    the feed calls "Kuala Lumpur". With no file for that venue, the Grand
    Prix name picked Sakhir, so the live map drew the wrong circuit. A
    venue the feed names that no file knows is unknown, not Sakhir."""
    assert circuit_for_session("Spanish Grand Prix", "Somewhere New") is None
    assert circuit_for_session("Bahrain Grand Prix", "Somewhere New") is None


def test_the_2026_bahrain_grand_prix_is_drawn_at_sepang() -> None:
    sepang = circuit_for_session("Bahrain Grand Prix", "Kuala Lumpur")
    assert sepang is not None and sepang.circuit_id == "sepang"
    assert sepang.outline is not None
    sakhir = circuit_for_session("Bahrain Grand Prix", "Sakhir")
    assert sakhir is not None and sakhir.circuit_id == "bahrain"
    # A historical session carries no venue: the name still means Sakhir.
    by_name = circuit_for_session("Bahrain Grand Prix", None)
    assert by_name is not None and by_name.circuit_id == "bahrain"


def test_a_live_session_finds_its_circuit_from_its_own_stream() -> None:
    """The live path end to end at the kernel seam: no event name is passed
    (the store never had one for a live session), yet the frames carry the
    right circuit, found from the stream's own `SessionInfoUpdated`."""
    from datetime import UTC, datetime

    from f1_domain import SessionInfoUpdated, SessionKey, SessionType
    from f1_kernel.frames import session_frames_from_events

    key = SessionKey(year=2026, round=15, session_type=SessionType.RACE)
    info = SessionInfoUpdated(
        session_key=key,
        ts_utc=datetime(2026, 9, 27, 11, 0, tzinfo=UTC),
        source_seq=1,
        meeting_name="Azerbaijan Grand Prix",
        circuit_short_name="Baku",
    )

    frames = session_frames_from_events([info], key)

    assert frames.circuit is not None and frames.circuit.circuit_id == "baku"
    assert frames.event_name == "Azerbaijan Grand Prix"


def _scheduled(event_name: str, location: str | None) -> str | None:
    circuit = circuit_for_schedule(event_name, location)
    return circuit.circuit_id if circuit is not None else None


@pytest.mark.parametrize(
    ("event_name", "location", "circuit_id"),
    [
        # Moved races resolve by venue, never by the Grand Prix name.
        ("Bahrain Grand Prix", "Kuala Lumpur", "sepang"),
        ("Bahrain Grand Prix", "Sakhir", "bahrain"),
        ("Spanish Grand Prix", "Madrid", "madring"),
        ("Spanish Grand Prix", "Barcelona", "barcelona"),
        # Respellings across seasons, and accents.
        ("Abu Dhabi Grand Prix", "Yas Marina", "yas_marina"),
        ("Abu Dhabi Grand Prix", "Yas Island", "yas_marina"),
        ("Canadian Grand Prix", "Montreal", "montreal"),
        ("Miami Grand Prix", "Miami Gardens", "miami"),
        # An unknown venue is never another track; no location uses the name.
        ("Bahrain Grand Prix", "Somewhere New", None),
        ("Singapore Grand Prix", None, "marina_bay"),
    ],
)
def test_circuit_for_schedule(
    event_name: str, location: str | None, circuit_id: str | None
) -> None:
    assert _scheduled(event_name, location) == circuit_id
