"""Circuit metadata: pit loss, corner geometry, DRS zones.

The data lives in versioned JSON under `f1_kernel/circuits/`, one file per
circuit, and is loaded into frozen pydantic models here. Per CLAUDE.md it is
"calibrated where possible, never guessed":

- `corners` and `lap_distance_m` are DERIVED from real session data (FastF1
  circuit info + reference-lap telemetry) by `tools/build_circuits.py`.
- `drs_zones` are derived from telemetry — the distance ranges where the DRS
  channel actually reads open on the fastest laps of several drivers.
  `detection_m` is null everywhere: detection points are timing-loop
  concepts that no car channel carries, so we do not have them and do not
  invent them.
- `pit_loss_s` is CALIBRATED from our own backfill where the data reaches:
  `f1_kernel.pit_loss` measures every green-flag stop's (in-lap + out-lap)
  delta against the driver's own clean neighbouring laps and takes the
  per-circuit median (`tools/calibrate_pit_loss.py`), with the uncertainty
  and sample count stored alongside. Circuits without enough measured stops
  keep a commonly-quoted PROVISIONAL figure; `pit_loss_provenance` says
  which one you are holding. `KernelParams.pit_loss_overrides_s` overrides
  either per circuit without editing checked-in data.

Only a few 2024 circuits ship today. `load_circuit` raises for anything
else and `circuit_for_event` returns None — an analytic must degrade
gracefully (fall back to `KernelParams.default_pit_loss_s`) rather than
assume every venue is present.
"""

from __future__ import annotations

import unicodedata
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from f1_kernel.track_outline import PitLane, TrackOutline

CIRCUITS_DIR = Path(__file__).parent / "circuits"


class Corner(BaseModel):
    """One numbered corner, positioned along the reference lap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int
    letter: str | None = None
    """Suffix for split corners ("7A"/"7B"), None for a plain number."""
    distance_m: float = Field(ge=0)
    """Distance from the start/finish line along the reference lap, metres."""
    angle_deg: float | None = None
    """Corner heading angle as FastF1 reports it. Informational."""

    @property
    def label(self) -> str:
        return f"{self.number}{self.letter or ''}"


class DrsZone(BaseModel):
    """One DRS activation zone, in reference-lap distance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int
    detection_m: float | None = None
    """Always None for now — not derivable from car telemetry. See module docs."""
    start_m: float = Field(ge=0)
    end_m: float = Field(ge=0)
    observed_drivers: int | None = None
    """How many sampled (driver, lap) telemetry traces agreed on this zone —
    a quality signal on the derivation, not a property of the circuit."""
    wraps_start_finish: bool = False
    """True for a zone that crosses the timing line (the main straight at
    most circuits). Then `start_m > end_m`: the zone runs from `start_m` to
    the end of the lap and continues from 0 to `end_m`."""

    def contains(self, distance_m: float) -> bool:
        if self.wraps_start_finish:
            return distance_m >= self.start_m or distance_m <= self.end_m
        return self.start_m <= distance_m <= self.end_m

    def length_m(self, lap_distance_m: float | None = None) -> float:
        """Zone length. A wrapping zone needs the lap distance to be
        measurable at all, and reports 0.0 without it rather than a
        negative number."""
        if not self.wraps_start_finish:
            return max(0.0, self.end_m - self.start_m)
        if lap_distance_m is None:
            return 0.0
        return max(0.0, lap_distance_m - self.start_m) + max(0.0, self.end_m)


class Circuit(BaseModel):
    """One circuit's metadata, as stored in `circuits/<circuit_id>.json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    circuit_id: str
    name: str
    country: str
    event_names: list[str] = Field(default_factory=list[str])
    """Grand Prix names that run here, as FastF1/the feed spell them — the
    lookup key from a historical session to its circuit."""
    feed_circuit_names: list[str] = Field(default_factory=list[str])
    """The feed's own name for the venue (`SessionInfo.Meeting.Circuit.
    ShortName`: "Catalunya", "Madring", "Baku"), recorded from FastF1 by
    `tools/build_circuits.py`. Preferred over `event_names` wherever a
    session carries it, because a Grand Prix name is not an identity: the
    "Spanish Grand Prix" ran at Barcelona through 2025 and at Madrid from
    2026, and a lookup by name alone would draw the wrong circuit."""
    schedule_locations: list[str] = Field(default_factory=list[str])
    """FastF1's event-schedule `Location` for rounds held here ("Marina
    Bay", "Budapest", "Miami Gardens"): how a calendar entry, which has no
    session yet, finds its circuit (`circuit_for_schedule`). Recorded by
    `tools/build_circuit_locations.py` from this circuit's own reference
    event and the 2023-2025 rounds held here."""
    lap_distance_m: float = Field(gt=0)
    """Lap distance from telemetry: the median over the same sampled racing
    laps the DRS zones were derived from, so both are on one scale.
    Approximate, and NOT the official published circuit length — distance is
    integrated from the speed channel and varies by a few metres with the
    line taken."""
    pit_loss_s: float = Field(gt=0)
    """Total time lost to a green-flag pit stop versus staying out, in
    seconds, measured over the in-lap + out-lap against clean neighbouring
    laps — so it INCLUDES the average fresh-tyre warm-up cost (lap times
    cannot separate pit-lane transit from warm-up). Calibrated per circuit by
    `tools/calibrate_pit_loss.py` where the backfill has enough green-flag
    stops; provisional otherwise."""
    pit_loss_provenance: str = "provisional"
    pit_loss_note: str = ""
    pit_loss_uncertainty_s: float | None = None
    """~1 standard error on the calibrated median (MAD-based), in seconds.
    None when `pit_loss_s` is provisional — an uncertainty on a quoted
    figure would be an invented number."""
    pit_loss_n_stops: int | None = None
    """How many green-flag stops the calibrated value rests on. None when
    provisional."""
    geometry_source: str = ""
    """Which session the corner/DRS/distance data was derived from."""
    corners: list[Corner] = Field(default_factory=list[Corner])
    drs_zones: list[DrsZone] = Field(default_factory=list[DrsZone])
    outline: TrackOutline | None = None
    """The real 2D lap shape, derived from position samples by
    `tools/build_circuits.py`'s outline step (`f1_kernel.track_outline`).
    None for a circuit that step has not yet been run against — Phase 3
    must render gracefully without one, the same "unknown is not excluded"
    discipline the rest of this package follows."""
    pit_lane: PitLane | None = None
    """The pit lane, derived from the reference race's real pit stops by
    `tools/build_pit_lanes.py` (`f1_kernel.track_outline.derive_pit_lane`).
    None until that tool has run for the circuit."""

    def corner_at(self, distance_m: float) -> Corner | None:
        """The next corner at or after `distance_m`, or None past the last
        one. What a braking-point analytic asks to name a braking event."""
        for corner in self.corners:
            if corner.distance_m >= distance_m:
                return corner
        return None

    def in_drs_zone(self, distance_m: float) -> DrsZone | None:
        """The DRS zone a point on the lap falls in, handling the zone that
        wraps the timing line."""
        for zone in self.drs_zones:
            if zone.contains(distance_m):
                return zone
        return None


def list_circuits() -> list[str]:
    """Every `circuit_id` that ships with the kernel, sorted."""
    return sorted(path.stem for path in CIRCUITS_DIR.glob("*.json"))


@cache
def load_circuit(circuit_id: str) -> Circuit:
    """Load one circuit by id. Raises `KeyError` if it isn't shipped —
    callers that can continue without it should use `circuit_for_event`,
    which returns None instead."""
    path = CIRCUITS_DIR / f"{circuit_id}.json"
    if not path.is_file():
        raise KeyError(
            f"no circuit metadata for {circuit_id!r}; shipped circuits: {list_circuits()}"
        )
    return Circuit.model_validate_json(path.read_text(encoding="utf-8"))


def circuit_for_session(
    event_name: str | None, feed_circuit_name: str | None = None
) -> Circuit | None:
    """The circuit a session ran at: by the feed's own circuit name when the
    session carries one (unambiguous across seasons), else by Grand Prix
    name. None when neither matches a shipped circuit.

    A venue the feed names but no file knows is NOT resolved by Grand Prix
    name to a circuit that records a different venue: that is another
    track. The 2026 "Bahrain Grand Prix" ran at Sepang ("Kuala Lumpur" in
    the feed), and falling back to the name drew it on Sakhir's outline
    with Sakhir's lap length and pit loss. An unknown circuit is honest;
    the wrong one is not. The name still decides when the matched file has
    no venue names of its own to contradict the feed."""
    if feed_circuit_name:
        wanted = feed_circuit_name.strip().casefold()
        for circuit_id in list_circuits():
            circuit = load_circuit(circuit_id)
            if any(name.strip().casefold() == wanted for name in circuit.feed_circuit_names):
                return circuit
        by_name = circuit_for_event(event_name)
        if by_name is not None and by_name.feed_circuit_names:
            return None
        return by_name
    return circuit_for_event(event_name)


def _fold(text: str) -> str:
    """Case-, space- and accent-insensitive form ("Montréal" == "Montreal")."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def circuit_for_schedule(event_name: str | None, location: str | None) -> Circuit | None:
    """The circuit a calendar entry will run at, by FastF1's schedule
    `Location` first (an identity across seasons, as the feed's venue name
    is), then by Grand Prix name. A location no file knows is never another
    track by the race's name: that would be `circuit_for_session`'s
    Sakhir-for-Sepang mistake again."""
    if location:
        wanted = _fold(location)
        circuits = [load_circuit(circuit_id) for circuit_id in list_circuits()]
        for circuit in circuits:
            names = [*circuit.schedule_locations, *circuit.feed_circuit_names]
            if any(_fold(name) == wanted for name in names):
                return circuit
        # Schedules respell a venue over the years ("Yas Island" in 2024,
        # "Yas Marina" in 2026, the feed's "Yas Marina Circuit"): a location
        # inside a known name still identifies it.
        for circuit in circuits:
            names = [*circuit.feed_circuit_names, circuit.name]
            if any(wanted in _fold(name) for name in names):
                return circuit
        by_name = circuit_for_event(event_name)
        if by_name is not None and (by_name.schedule_locations or by_name.feed_circuit_names):
            return None
        return by_name
    return circuit_for_event(event_name)


def circuit_for_event(event_name: str | None) -> Circuit | None:
    """Find a circuit by Grand Prix name (case/whitespace-insensitive),
    or None when the event isn't one of the few that ship. Matching is on
    `event_names`, since the same circuit is named differently across
    seasons ("Emilia Romagna" vs "San Marino")."""
    if not event_name:
        return None
    wanted = event_name.strip().casefold()
    for circuit_id in list_circuits():
        circuit = load_circuit(circuit_id)
        if any(name.strip().casefold() == wanted for name in circuit.event_names):
            return circuit
        if circuit.circuit_id.casefold() == wanted or circuit.name.strip().casefold() == wanted:
            return circuit
    return None
