"""One-time (but re-runnable) offline calibration of per-circuit pit loss.

    uv run python python/f1_kernel/tools/calibrate_pit_loss.py --seasons 2024
    uv run python python/f1_kernel/tools/calibrate_pit_loss.py --seasons 2024 --write-circuits

Per CLAUDE.md's Phase 2 invariant ("no hardcoded magic numbers... all
CALIBRATED from historical data"), this measures what a green-flag stop
actually costs at each circuit — see `f1_kernel.pit_loss` for the method
(per-stop (in+out) - clean-neighbour baselines, median per circuit,
MAD-based uncertainty; includes average out-lap warm-up by construction).

This script's OUTPUT is what is committed, four ways:

1. `python/f1_kernel/calibration/pit_loss.json` — the full record (every
   per-stop sample, per-circuit medians, uncertainties, exclusion counts)
   so the numbers are auditable without re-running anything.
2. `python/f1_kernel/reports/pit_loss.md` — the human-readable version of
   the same run, part of the committed Phase 2 backtest suite
   `tools/run_backtest_suite.py` regenerates alongside the degradation and
   undercut reports.
3. With `--write-circuits`, the shipped `f1_kernel/circuits/*.json` files
   whose circuit cleared the sample floor get `pit_loss_s`,
   `pit_loss_provenance="calibrated"`, `pit_loss_uncertainty_s` and
   `pit_loss_n_stops` updated in place — then REVIEWED via git diff before
   committing, the same flow as `tools/build_circuits.py`.
4. `KernelParams.default_pit_loss_s` (the no-circuit-file fallback) — the
   suggested cross-circuit median is printed; the literal default in
   `params.py` is updated BY HAND after inspecting it, the same review step
   `calibrate_fuel.py`'s output goes through.

Dev-only: needs `data/historical/` populated (`uv run f1-backfill --season
2024`, ...). Never imported by the kernel at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from f1_domain import SessionType
from f1_kernel.circuit import CIRCUITS_DIR, Circuit
from f1_kernel.params import KernelParams
from f1_kernel.pit_loss import PitLossCalibrationResult, calibrate_pit_loss
from f1_kernel.sources import HistoricalSource, list_historical_sessions

DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"
OUTPUT_PATH = Path(__file__).parents[1] / "calibration" / "pit_loss.json"
REPORT_PATH = Path(__file__).parents[1] / "reports" / "pit_loss.md"
DEFAULT_SEASONS = (2023, 2024, 2025)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="historical backfill root")
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=list(DEFAULT_SEASONS),
        help="seasons to include (default: 2023 2024 2025, whatever exists is used)",
    )
    parser.add_argument("--min-stops", type=int, default=8, help="calibration floor per circuit")
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    parser.add_argument(
        "--write-circuits",
        action="store_true",
        help="patch shipped circuits/*.json for circuits that cleared the floor",
    )
    args = parser.parse_args(argv[1:])

    source = HistoricalSource(args.root)
    sessions = [
        ref.session_key
        for ref in list_historical_sessions(
            args.root, session_types=[SessionType.RACE], seasons=args.seasons
        )
    ]
    if not sessions:
        print(
            f"no backfilled races found under {args.root} for seasons {args.seasons}; "
            "run `uv run f1-backfill --season <year>` first",
            file=sys.stderr,
        )
        return 1

    print(f"measuring pit loss over {len(sessions)} race(s): {sorted({s.year for s in sessions})}")
    result = calibrate_pit_loss(
        source, sessions, KernelParams(), min_stops_per_circuit=args.min_stops
    )

    print(f"\n{result.n_samples} green-flag stop(s) measured; exclusions: {result.excluded}")
    print(f"{'circuit':<28} {'pit_loss_s':>10} {'±SE':>6} {'MAD':>6} {'stops':>6}  seasons")
    for circuit in sorted(result.circuits, key=lambda c: c.pit_loss_s):
        marker = "  (thin — keep provisional)" if circuit.thin else ""
        print(
            f"{circuit.circuit_key:<28} {circuit.pit_loss_s:>10.2f} "
            f"{circuit.uncertainty_s:>6.2f} {circuit.mad_s:>6.2f} {circuit.n_stops:>6}  "
            f"{','.join(str(s) for s in circuit.seasons)}{marker}"
        )
    if result.suggested_default_pit_loss_s is not None:
        print(
            f"\nsuggested KernelParams.default_pit_loss_s = "
            f"{result.suggested_default_pit_loss_s:.2f} "
            "(cross-circuit median; update params.py by hand after review)"
        )

    _write_artifact(result, Path(args.out), args.seasons, args.min_stops)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(result, args.seasons, args.min_stops), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    if args.write_circuits:
        _patch_circuit_files(result)
    return 0


def _render_report(result: PitLossCalibrationResult, seasons: Sequence[int], min_stops: int) -> str:
    lines = [
        "# Pit-loss calibration report",
        "",
        f"Generated by `python/f1_kernel/tools/calibrate_pit_loss.py` over "
        f"{result.n_samples} green-flag stop(s) across {result.n_sessions} session(s) "
        f"(seasons requested: {', '.join(str(s) for s in seasons)}; "
        f"calibration floor: {min_stops} stops/circuit).",
        "",
        "Per stop: (in-lap + out-lap) minus clean-neighbour baselines (median of the clean "
        "laps just before the stop, same stint, plus just after the out-lap, new stint), "
        "fuel-corrected — see `f1_kernel.pit_loss` for the full method. SC/VSC/yellow/red "
        "stops, drive-throughs and double-stops are excluded and counted below; by "
        "construction the number includes the average out-lap warm-up.",
        "",
        "## Per-circuit results",
        "",
        "| circuit | pit_loss_s | ±SE | MAD | stops | sessions | seasons | status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for circuit in sorted(result.circuits, key=lambda c: c.pit_loss_s):
        status = "thin — kept provisional" if circuit.thin else "CALIBRATED"
        seasons_str = ",".join(str(s) for s in circuit.seasons)
        lines.append(
            f"| {circuit.circuit_key} | {circuit.pit_loss_s:.2f} | {circuit.uncertainty_s:.2f} "
            f"| {circuit.mad_s:.2f} | {circuit.n_stops} | {circuit.n_sessions} "
            f"| {seasons_str} | {status} |"
        )
    lines += ["", "## Exclusions", "", "| reason | count |", "| --- | --- |"]
    for reason, count in sorted(result.excluded.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {reason} | {count} |")
    suggestion = (
        f"**{result.suggested_default_pit_loss_s:.2f}**s (cross-circuit median of the "
        "non-thin per-circuit medians)"
        if result.suggested_default_pit_loss_s is not None
        else "_no circuit cleared the sample floor — no suggestion._"
    )
    lines += ["", "## Suggested `KernelParams.default_pit_loss_s`", "", suggestion, ""]
    return "\n".join(lines)


def _write_artifact(
    result: PitLossCalibrationResult, out_path: Path, seasons: list[int], min_stops: int
) -> None:
    document = {
        "method": result.method,
        "seasons_requested": seasons,
        "min_stops_per_circuit": min_stops,
        "n_samples": result.n_samples,
        "n_sessions": result.n_sessions,
        "suggested_default_pit_loss_s": result.suggested_default_pit_loss_s,
        "excluded": result.excluded,
        "circuits": [
            {
                "circuit_key": c.circuit_key,
                "circuit_id": c.circuit_id,
                "event_names": list(c.event_names),
                "pit_loss_s": c.pit_loss_s,
                "mad_s": c.mad_s,
                "uncertainty_s": c.uncertainty_s,
                "n_stops": c.n_stops,
                "n_sessions": c.n_sessions,
                "seasons": list(c.seasons),
                "thin": c.thin,
            }
            for c in result.circuits
        ],
        "samples": [
            {
                "session_year": s.session_year,
                "session_round": s.session_round,
                "session_type": s.session_type,
                "event_name": s.event_name,
                "driver": s.driver,
                "in_lap": s.in_lap,
                "out_lap": s.out_lap,
                "pit_loss_s": s.pit_loss_s,
                "n_pre_laps": s.n_pre_laps,
                "n_post_laps": s.n_post_laps,
            }
            for s in result.samples
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")


def _patch_circuit_files(result: PitLossCalibrationResult) -> None:
    """Update shipped circuit JSON for every non-thin calibrated circuit.
    The diff is the review surface — same flow as `build_circuits.py`."""
    for estimate in result.circuits:
        if estimate.circuit_id is None:
            continue
        if estimate.thin:
            print(f"  {estimate.circuit_id}: only {estimate.n_stops} stop(s) — left provisional")
            continue
        path = CIRCUITS_DIR / f"{estimate.circuit_id}.json"
        circuit = Circuit.model_validate_json(path.read_text(encoding="utf-8"))
        seasons = ", ".join(str(s) for s in estimate.seasons)
        updated = circuit.model_copy(
            update={
                "pit_loss_s": round(estimate.pit_loss_s, 2),
                "pit_loss_provenance": "calibrated",
                "pit_loss_note": (
                    f"Calibrated from {estimate.n_stops} green-flag stops ({seasons}): "
                    "median per-stop (in+out) - clean-neighbour baselines, fuel-corrected; "
                    "includes average out-lap warm-up. tools/calibrate_pit_loss.py."
                ),
                "pit_loss_uncertainty_s": round(estimate.uncertainty_s, 2),
                "pit_loss_n_stops": estimate.n_stops,
            }
        )
        path.write_text(
            json.dumps(updated.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )
        print(f"  wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
