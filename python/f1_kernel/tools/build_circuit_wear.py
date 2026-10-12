"""Measure each circuit's tyre wear per compound from the backfilled races
and record it in the circuit files (`Circuit.tyre_wear`), for the circuit
pages (ROADMAP S2).

For every backfilled race of `SEASONS` the kernel fits degradation per stint
(`f1_kernel.analyze_session`); only fits rated `good` count, and each
compound's figure is their median rate, fuel-corrected, with how many fits
and which races it rests on. 2023-2025 only: those seasons have no venue
moves under one Grand Prix name, so a race finds its circuit by name. Dry
compounds only: on intermediates and wets the track drying out makes laps
quicker as the tyre ages (2024 Montreal measured -0.39 s/lap), which is not
tyre wear.

    uv run python python/f1_kernel/tools/build_circuit_wear.py

The backfill can be split across machines (2026-10-12: 2024 here, 2025 on
the VM). `--fits-out` writes the good fits measured from one backfill as
JSON instead of the circuit files; `--fits-in` adds such a file's fits to
the ones measured here before the medians are taken, so the figures rest
on every race either machine holds:

    # on the VM, over its backfill
    python build_circuit_wear.py --historical /app/data/historical \
        --seasons 2025 --fits-out fits-2025.json
    # here
    uv run python python/f1_kernel/tools/build_circuit_wear.py \
        --seasons 2024 --fits-in fits-2025.json
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from f1_domain import SessionKey, SessionType
from f1_kernel import KernelParams, Reliability, analyze_session, circuit_for_event
from f1_kernel.circuit import CIRCUITS_DIR, list_circuits
from f1_kernel.sources import HistoricalSource

SEASONS = (2023, 2024, 2025)
HISTORICAL = Path("data/historical")
DRY = ("soft", "medium", "hard")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, default=HISTORICAL)
    parser.add_argument(
        "--seasons", type=int, nargs="*", default=list(SEASONS), help="none: only merge --fits-in"
    )
    parser.add_argument("--fits-out", type=Path, help="write the fits as JSON, not the circuits")
    parser.add_argument("--fits-in", type=Path, nargs="*", default=[], help="fits to add")
    args = parser.parse_args()
    logging.getLogger("fastf1").setLevel(logging.WARNING)
    manifest = json.loads((args.historical / "manifest.json").read_text(encoding="utf-8"))
    source = HistoricalSource(args.historical)
    rates: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    races: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for entry in manifest["sessions"].values():
        if entry["session_type"] != "Race" or entry["season"] not in args.seasons:
            continue
        circuit = circuit_for_event(entry["event_name"])
        if circuit is None:
            continue
        key = SessionKey(year=entry["season"], round=entry["round"], session_type=SessionType.RACE)
        try:
            report = analyze_session(source, key, KernelParams())
        except Exception as error:  # one unreadable race must not stop the rest
            print(f"skip {key}: {error}")
            continue
        for fit in report.degradation_fits:
            if fit.reliability is not Reliability.GOOD:
                continue
            compound = str(fit.compound).lower()
            if compound not in DRY:
                continue
            rates[circuit.circuit_id][compound].append(fit.deg_rate_s_per_lap)
            races[circuit.circuit_id][compound].add(f"{key.year}-{key.round}")
        print(f"measured {key.year}-{key.round} {entry['event_name']}")

    if args.fits_out is not None:
        fits = {
            circuit_id: {
                compound: {"rates": values, "sessions": sorted(races[circuit_id][compound])}
                for compound, values in by_compound.items()
            }
            for circuit_id, by_compound in rates.items()
        }
        args.fits_out.write_text(json.dumps(fits, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.fits_out}")
        return 0
    for extra in args.fits_in:
        fits_in: dict[str, dict[str, dict[str, list[Any]]]] = json.loads(
            extra.read_text(encoding="utf-8")
        )
        for circuit_id, by_compound in fits_in.items():
            for compound, fit in by_compound.items():
                rates[circuit_id][compound].extend(float(v) for v in fit["rates"])
                races[circuit_id][compound].update(str(s) for s in fit["sessions"])
        print(f"added {extra}")

    for circuit_id in list_circuits():
        path = CIRCUITS_DIR / f"{circuit_id}.json"
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        wear = {
            compound: {
                "median_s_per_lap": round(statistics.median(values), 4),
                "good_fits": len(values),
                "sessions": sorted(races[circuit_id][compound]),
            }
            for compound, values in sorted(rates[circuit_id].items())
            if values
        }
        rebuilt: dict[str, Any] = {}
        for key_name, value in document.items():
            if key_name == "tyre_wear":
                continue
            rebuilt[key_name] = value
            if key_name == "schedule_locations":
                rebuilt["tyre_wear"] = wear
        path.write_text(json.dumps(rebuilt, indent=2) + "\n", encoding="utf-8")
        summary = ", ".join(
            f"{c} {w['median_s_per_lap']:+.3f} ({w['good_fits']})" for c, w in wear.items()
        )
        print(f"{circuit_id:14} {summary or 'no races measured'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
