"""Regenerate the checked-in circuit metadata from real session data.

    uv run python python/f1_kernel/tools/build_circuits.py            # all
    uv run python python/f1_kernel/tools/build_circuits.py bahrain    # one

CLAUDE.md requires circuit metadata to be "calibrated where possible, never
guessed", so nothing in `f1_kernel/circuits/*.json` is typed in by hand
except the values that genuinely cannot be derived:

- **corners** come from FastF1's own circuit info (`get_circuit_info()`),
  which carries each corner's real distance along the reference lap;
- **lap distance** is the reference lap's telemetry distance;
- **DRS zones** are DERIVED from telemetry: the distance ranges where the
  DRS channel reads open (codes 10/12/14) on the fastest lap of several
  drivers, intersected across drivers so one driver's odd activation can't
  invent a zone. Detection points are NOT in the data (detection is a
  timing-loop concept, not a car channel) and stay null.
- **pit loss** cannot be derived from FastF1 at all and stays PROVISIONAL —
  see `docs/PHASE2.md` for the calibration path.
- **outline** (Phase 3.4) is the real 2D lap shape, derived from the
  reference lap's own position telemetry (FastF1's `X`/`Y` columns — the
  same channel `f1_domain.CarPosition` documents for the live feed, just
  replayed from F1's official API instead of the SignalR socket) via
  `f1_kernel.track_outline.derive_track_outline`. Skipped (left `null`) for
  a session whose position telemetry FastF1 cannot provide, printed as a
  warning rather than failing the whole circuit — a missing shape is not
  a missing corner list.

Dev-only: it imports `fastf1` (a `f1_backfill` dependency, not a kernel
one) and needs the FastF1 cache populated — it is never imported by the
kernel at runtime, which only reads the JSON it writes.
"""

from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from f1_kernel.track_outline import derive_track_outline

OUTPUT_DIR = Path(__file__).parents[1] / "src" / "f1_kernel" / "circuits"
CACHE_DIR = Path(__file__).parents[3] / "data" / "fastf1_cache"

# DRS channel codes that mean "flap open" (see f1_domain.CarTelemetry.drs).
DRS_OPEN_CODES = (10, 12, 14)
# Ignore blips shorter than this; a real zone is hundreds of metres.
MIN_ZONE_LENGTH_M = 120.0
# Sampling budget for the DRS derivation: drivers, laps each, and the
# stride between sampled laps (spread over the race, not one clump).
DRS_SAMPLE_DRIVERS = 8
DRS_SAMPLE_LAPS = 12
DRS_LAP_STRIDE = 3
# A zone must be seen this many times across all (driver, lap) samples.
DRS_MIN_OBSERVATIONS = 5
# How close to the start/finish line counts as touching it, for detecting a
# zone that wraps around lap 0.
WRAP_TOLERANCE_M = 60.0


@dataclass(frozen=True)
class CircuitSpec:
    """What we must state by hand: identity, which session to derive
    geometry from, and the pit loss we cannot derive."""

    circuit_id: str
    name: str
    country: str
    event_names: tuple[str, ...]
    season: int
    round_no: int
    session: str
    pit_loss_s: float
    pit_loss_note: str


# Pit-loss figures are PROVISIONAL: the widely-quoted total time lost
# (pit-entry line to pit-exit line versus staying on track) at each venue.
# They are the placeholder a later prompt replaces with a value measured
# off our own backfill.
SPECS: tuple[CircuitSpec, ...] = (
    CircuitSpec(
        circuit_id="bahrain",
        name="Bahrain International Circuit",
        country="Bahrain",
        event_names=("Bahrain Grand Prix",),
        season=2024,
        round_no=1,
        session="Race",
        pit_loss_s=21.5,
        pit_loss_note="Provisional: commonly quoted ~21-22s total loss.",
    ),
    CircuitSpec(
        circuit_id="jeddah",
        name="Jeddah Corniche Circuit",
        country="Saudi Arabia",
        event_names=("Saudi Arabian Grand Prix",),
        season=2024,
        round_no=2,
        session="Race",
        pit_loss_s=19.5,
        pit_loss_note="Provisional: short pit lane, commonly quoted ~19-20s.",
    ),
    CircuitSpec(
        circuit_id="albert_park",
        name="Albert Park Circuit",
        country="Australia",
        event_names=("Australian Grand Prix",),
        season=2024,
        round_no=3,
        session="Race",
        pit_loss_s=20.5,
        pit_loss_note="Provisional: commonly quoted ~20-21s.",
    ),
    CircuitSpec(
        circuit_id="suzuka",
        name="Suzuka International Racing Course",
        country="Japan",
        event_names=("Japanese Grand Prix",),
        season=2024,
        round_no=4,
        session="Race",
        pit_loss_s=22.5,
        pit_loss_note="Provisional: long pit lane, commonly quoted ~22-23s.",
    ),
    CircuitSpec(
        circuit_id="monaco",
        name="Circuit de Monaco",
        country="Monaco",
        event_names=("Monaco Grand Prix",),
        season=2024,
        round_no=8,
        session="Race",
        pit_loss_s=19.0,
        pit_loss_note="Provisional: commonly quoted ~19-20s.",
    ),
    CircuitSpec(
        circuit_id="monza",
        name="Autodromo Nazionale Monza",
        country="Italy",
        event_names=("Italian Grand Prix",),
        season=2024,
        round_no=16,
        session="Race",
        pit_loss_s=24.0,
        pit_loss_note="Provisional: long pit lane, commonly quoted ~23-25s.",
    ),
    # The rest of the calendar (added 2026-09-23 so every race has a map).
    # Pit loss starts from the measured 2024 median in calibration/pit_loss.json;
    # `calibrate_pit_loss.py --write-circuits` marks it calibrated where the
    # sample clears the floor, and it stays provisional where it does not.
    CircuitSpec(
        circuit_id="shanghai",
        name="Shanghai International Circuit",
        country="China",
        event_names=("Chinese Grand Prix",),
        season=2024,
        round_no=5,
        session="Race",
        pit_loss_s=23.33,
        pit_loss_note=(
            "Measured median of 9 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="miami",
        name="Miami International Autodrome",
        country="United States",
        event_names=("Miami Grand Prix",),
        season=2024,
        round_no=6,
        session="Race",
        pit_loss_s=20.08,
        pit_loss_note=(
            "Measured median of 5 green-flag stops (2024), below the 8-stop calibration floor, so"
            " provisional. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="imola",
        name="Autodromo Enzo e Dino Ferrari",
        country="Italy",
        event_names=("Emilia Romagna Grand Prix",),
        season=2024,
        round_no=7,
        session="Race",
        pit_loss_s=28.5,
        pit_loss_note=(
            "Measured median of 18 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="montreal",
        name="Circuit Gilles Villeneuve",
        country="Canada",
        event_names=("Canadian Grand Prix",),
        season=2024,
        round_no=9,
        session="Race",
        pit_loss_s=26.28,
        pit_loss_note=(
            "Measured median of 8 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="barcelona",
        name="Circuit de Barcelona-Catalunya",
        country="Spain",
        event_names=("Spanish Grand Prix", "Barcelona-Catalunya Grand Prix"),
        season=2024,
        round_no=10,
        session="Race",
        pit_loss_s=23.03,
        pit_loss_note=(
            "Measured median of 35 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="red_bull_ring",
        name="Red Bull Ring",
        country="Austria",
        event_names=("Austrian Grand Prix",),
        season=2024,
        round_no=11,
        session="Race",
        pit_loss_s=21.0,
        pit_loss_note=(
            "Measured median of 31 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="silverstone",
        name="Silverstone Circuit",
        country="United Kingdom",
        event_names=("British Grand Prix",),
        season=2024,
        round_no=12,
        session="Race",
        pit_loss_s=26.42,
        pit_loss_note=(
            "Measured median of 33 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="hungaroring",
        name="Hungaroring",
        country="Hungary",
        event_names=("Hungarian Grand Prix",),
        season=2024,
        round_no=13,
        session="Race",
        pit_loss_s=21.33,
        pit_loss_note=(
            "Measured median of 27 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="spa",
        name="Circuit de Spa-Francorchamps",
        country="Belgium",
        event_names=("Belgian Grand Prix",),
        season=2024,
        round_no=14,
        session="Race",
        pit_loss_s=18.78,
        pit_loss_note=(
            "Measured median of 18 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="zandvoort",
        name="Circuit Zandvoort",
        country="Netherlands",
        event_names=("Dutch Grand Prix",),
        season=2024,
        round_no=15,
        session="Race",
        pit_loss_s=23.54,
        pit_loss_note=(
            "Measured median of 21 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="baku",
        name="Baku City Circuit",
        country="Azerbaijan",
        event_names=("Azerbaijan Grand Prix",),
        season=2024,
        round_no=17,
        session="Race",
        pit_loss_s=22.29,
        pit_loss_note=(
            "Measured median of 11 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="marina_bay",
        name="Marina Bay Street Circuit",
        country="Singapore",
        event_names=("Singapore Grand Prix",),
        season=2024,
        round_no=18,
        session="Race",
        pit_loss_s=30.13,
        pit_loss_note=(
            "Measured median of 18 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="cota",
        name="Circuit of the Americas",
        country="United States",
        event_names=("United States Grand Prix",),
        season=2024,
        round_no=19,
        session="Race",
        pit_loss_s=21.7,
        pit_loss_note=(
            "Measured median of 20 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="mexico_city",
        name="Autodromo Hermanos Rodriguez",
        country="Mexico",
        event_names=("Mexico City Grand Prix",),
        season=2024,
        round_no=20,
        session="Race",
        pit_loss_s=23.5,
        pit_loss_note=(
            "Measured median of 17 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="interlagos",
        name="Autodromo Jose Carlos Pace",
        country="Brazil",
        event_names=("São Paulo Grand Prix",),
        season=2024,
        round_no=21,
        session="Race",
        pit_loss_s=23.03,
        pit_loss_note=(
            "No green-flag stops to measure in 2024 (a wet, red-flagged race), so provisional at "
            "the cross-circuit median 23.03 s. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="las_vegas",
        name="Las Vegas Strip Circuit",
        country="United States",
        event_names=("Las Vegas Grand Prix",),
        season=2024,
        round_no=22,
        session="Race",
        pit_loss_s=22.54,
        pit_loss_note=(
            "Measured median of 22 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="lusail",
        name="Lusail International Circuit",
        country="Qatar",
        event_names=("Qatar Grand Prix",),
        season=2024,
        round_no=23,
        session="Race",
        pit_loss_s=27.69,
        pit_loss_note=(
            "Measured median of 2 green-flag stops (2024), below the 8-stop calibration floor, so"
            " provisional. See calibration/pit_loss.json."
        ),
    ),
    CircuitSpec(
        circuit_id="yas_marina",
        name="Yas Marina Circuit",
        country="United Arab Emirates",
        event_names=("Abu Dhabi Grand Prix",),
        season=2024,
        round_no=24,
        session="Race",
        pit_loss_s=22.87,
        pit_loss_note=(
            "Measured median of 16 green-flag stops (2024), pending `calibrate_pit_loss.py "
            "--write-circuits`. See calibration/pit_loss.json."
        ),
    ),
    # New for 2026. No Grand Prix name on purpose: "Spanish Grand Prix" is
    # Barcelona's name through 2025 (see `barcelona`), so Madring is found
    # only by the feed's own venue name, which is unambiguous.
    CircuitSpec(
        circuit_id="madring",
        name="Madring",
        country="Spain",
        event_names=(),
        season=2026,
        round_no=14,
        session="Race",
        pit_loss_s=23.03,
        pit_loss_note=(
            "New venue with no measured stops yet: provisional at the 2024 cross-circuit "
            "median. See calibration/pit_loss.json."
        ),
    ),
)


def main(argv: list[str]) -> int:
    warnings.filterwarnings("ignore")
    import fastf1

    fastf1.Cache.enable_cache(str(CACHE_DIR))

    wanted = set(argv[1:])
    specs = [s for s in SPECS if not wanted or s.circuit_id in wanted]
    if not specs:
        print(f"no circuit matches {sorted(wanted)}", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for spec in specs:
        try:
            document = build_circuit(fastf1, spec)
        except Exception as exc:
            failures.append(f"{spec.circuit_id}: {type(exc).__name__}: {exc}")
            print(f"  FAILED {spec.circuit_id}: {exc}", file=sys.stderr)
            continue
        path = OUTPUT_DIR / f"{spec.circuit_id}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        outline_note = (
            f"{document['outline']['source_points']}-point outline"
            if document["outline"]
            else "no outline"
        )
        print(
            f"  wrote {path.name}: {len(document['corners'])} corners, "
            f"{len(document['drs_zones'])} DRS zones, "
            f"{document['lap_distance_m']:.0f} m reference lap, {outline_note}"
        )

    if failures:
        print(f"\n{len(failures)} circuit(s) failed:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    return 0


def build_circuit(fastf1: Any, spec: CircuitSpec) -> dict[str, Any]:
    print(f"{spec.circuit_id}: loading {spec.season} r{spec.round_no} {spec.session} ...")
    session = fastf1.get_session(spec.season, spec.round_no, spec.session)
    session.load(laps=True, telemetry=True, weather=False, messages=False)

    # FastF1 takes corners from a circuit-info service that lags a brand-new
    # venue (Madring, 2026). No corner list is the honest answer then --
    # corners are never guessed -- and it must not cost the venue its map:
    # the outline and DRS zones come from telemetry, which is there.
    try:
        info = session.get_circuit_info()
    except Exception:
        info = None
    if info is None:
        print(f"  {spec.circuit_id}: no circuit info from FastF1; shipping without corners")
        corners: list[dict[str, Any]] = []
    else:
        corners = [
            {
                "number": int(row["Number"]),
                "letter": str(row["Letter"]) or None,
                "distance_m": round(float(row["Distance"]), 1),
                "angle_deg": round(float(row["Angle"]), 1),
            }
            for _, row in info.corners.iterrows()
        ]

    # Lap distance and DRS zones come from the SAME sample of laps. Telemetry
    # distance is integrated per lap, so it varies by a few metres between
    # laps; taking the length from one lap and the zones from others puts
    # them on different scales and a zone can end up "past" the lap end.
    sampled = _sample_laps(session)
    lap_distance_m = round(_median([total for total, _ in sampled]), 1)

    return {
        "circuit_id": spec.circuit_id,
        "name": spec.name,
        "country": spec.country,
        "event_names": list(spec.event_names),
        "feed_circuit_names": feed_circuit_names(session),
        "lap_distance_m": lap_distance_m,
        "pit_loss_s": spec.pit_loss_s,
        "pit_loss_provenance": "provisional",
        "pit_loss_note": spec.pit_loss_note,
        "geometry_source": (
            f"fastf1 circuit info + telemetry, {spec.season} round {spec.round_no} {spec.session}"
        ),
        "corners": corners,
        "drs_zones": derive_drs_zones(sampled, lap_distance_m),
        "outline": _build_outline(session, spec),
    }


def feed_circuit_names(session: Any) -> list[str]:
    """The feed's own name for the venue (`Meeting.Circuit.ShortName`), the
    identity a live session carries. Empty when FastF1 does not expose it,
    in which case the circuit is still found by Grand Prix name."""
    try:
        short_name = session.session_info["Meeting"]["Circuit"]["ShortName"]
    except (AttributeError, KeyError, TypeError):
        return []
    return [str(short_name)] if short_name else []


def _build_outline(session: Any, spec: CircuitSpec) -> dict[str, Any] | None:
    """The real 2D lap shape from the session's fastest lap's own position
    telemetry — the same single-clean-lap choice already made for other
    reference-lap derivations in this tool. Returns None (never raises) on
    a session FastF1 cannot give position telemetry for; a missing shape
    should not fail the corners/DRS-zone derivation that already succeeded."""
    try:
        fastest = session.laps.pick_fastest()
        telemetry = fastest.get_telemetry()
        xy = [(float(x), float(y)) for x, y in zip(telemetry["X"], telemetry["Y"], strict=True)]
        outline = derive_track_outline(xy)
    except Exception as exc:
        print(f"    outline skipped for {spec.circuit_id}: {type(exc).__name__}: {exc}")
        return None
    return outline.model_dump(mode="json")


def _sample_laps(session: Any) -> list[tuple[float, list[tuple[float, float]]]]:
    """`(lap distance, DRS-open ranges)` for a sample of racing laps.

    Sampled across MANY laps and drivers, not just the fastest lap: a
    fastest lap is usually set in clean air with DRS never enabled, so it
    sees no zones at all.
    """
    sampled: list[tuple[float, list[tuple[float, float]]]] = []
    for driver in list(session.drivers)[:DRS_SAMPLE_DRIVERS]:
        laps = session.laps.pick_drivers(driver)
        if laps.empty:
            continue
        # Skip pit-adjacent laps (partial distance) and spread the sample
        # over the race so one traffic-free phase can't dominate.
        racing = laps[laps["PitInTime"].isna() & laps["PitOutTime"].isna()]
        for _, lap in list(racing.iterrows())[::DRS_LAP_STRIDE][:DRS_SAMPLE_LAPS]:
            try:
                telemetry = lap.get_car_data().add_distance()
            except Exception:
                continue
            distance = [float(d) for d in telemetry["Distance"]]
            if not distance:
                continue
            drs = [int(v) for v in telemetry["DRS"]]
            sampled.append((max(distance), _open_ranges(distance, drs)))
    if not sampled:
        raise RuntimeError("no usable telemetry laps in this session")
    return sampled


def derive_drs_zones(
    sampled: list[tuple[float, list[tuple[float, float]]]], lap_distance_m: float
) -> list[dict[str, Any]]:
    """DRS activation zones, from where the flap is actually open.

    Ranges are clustered by overlap and a zone is kept only when several
    independent (driver, lap) samples agree — one driver's odd activation
    cannot invent a zone, and a driver stuck out of range cannot delete one.

    A zone spanning the start/finish line shows up as two clusters (one
    ending at the lap distance, one starting at ~0); they are merged into a
    single wrapping zone rather than reported as two.
    """
    samples: list[tuple[float, float]] = [
        zone_range for _total, ranges in sampled for zone_range in ranges
    ]
    sampled_laps = len(sampled)

    if not samples:
        print("    no DRS activation observed; shipping an empty zone list")
        return []

    clusters: list[list[tuple[float, float]]] = []
    for start, end in samples:
        for cluster in clusters:
            if any(start < c_end and end > c_start for c_start, c_end in cluster):
                cluster.append((start, end))
                break
        else:
            clusters.append([(start, end)])

    kept = [cluster for cluster in clusters if len(cluster) >= DRS_MIN_OBSERVATIONS]
    kept.sort(key=lambda cluster: _median([s for s, _ in cluster]))

    zones: list[dict[str, Any]] = [
        {
            "number": 0,  # renumbered below, in track order
            "detection_m": None,
            # Drivers open DRS at the activation line within a few metres of
            # each other, so the median start is sharp. The END is where
            # each driver lifted, which varies, so take a high percentile to
            # recover the zone's real extent without chasing one outlier —
            # clamped to the lap, since per-lap integrated distance varies by
            # a few metres and a zone cannot run past the timing line.
            "start_m": round(min(_median([s for s, _ in cluster]), lap_distance_m), 1),
            "end_m": round(min(_percentile([e for _, e in cluster], 0.9), lap_distance_m), 1),
            "observed_drivers": len(cluster),
            "wraps_start_finish": False,
        }
        for cluster in kept
    ]

    zones = _merge_wrapping_zone(zones, lap_distance_m)
    for index, zone in enumerate(zones, start=1):
        zone["number"] = index
    print(f"    {len(zones)} DRS zone(s) from {sampled_laps} sampled laps")
    return zones


def _merge_wrapping_zone(
    zones: list[dict[str, Any]], lap_distance_m: float
) -> list[dict[str, Any]]:
    """Fold a zone that crosses the start/finish line back into one entry.

    It appears as a cluster starting at ~0 and another ending at ~the lap
    distance; the merged zone keeps `start_m > end_m` and is flagged, which
    is what `Circuit.in_drs_zone` understands."""
    if len(zones) < 2:
        return zones
    first, last = zones[0], zones[-1]
    starts_at_line = first["start_m"] <= WRAP_TOLERANCE_M
    ends_at_line = last["end_m"] >= lap_distance_m - WRAP_TOLERANCE_M
    if not (starts_at_line and ends_at_line):
        return zones
    merged = {
        "number": 0,
        "detection_m": None,
        "start_m": last["start_m"],
        "end_m": first["end_m"],
        "observed_drivers": first["observed_drivers"] + last["observed_drivers"],
        "wraps_start_finish": True,
    }
    return [*zones[1:-1], merged]


def _open_ranges(distance: list[float], drs: list[int]) -> list[tuple[float, float]]:
    """Contiguous distance ranges where the DRS channel reads open."""
    ranges: list[tuple[float, float]] = []
    start: float | None = None
    for position, code in zip(distance, drs, strict=False):
        if code in DRS_OPEN_CODES:
            if start is None:
                start = position
        elif start is not None:
            if position - start >= MIN_ZONE_LENGTH_M:
                ranges.append((start, position))
            start = None
    if start is not None and distance and distance[-1] - start >= MIN_ZONE_LENGTH_M:
        ranges.append((start, distance[-1]))
    return ranges


def _median(values: list[float]) -> float:
    return _percentile(values, 0.5)


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile — no interpolation, so the result is always
    an actually-observed value."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if fraction == 0.5 and len(ordered) % 2 == 0:
        middle = len(ordered) // 2
        return (ordered[middle - 1] + ordered[middle]) / 2
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
