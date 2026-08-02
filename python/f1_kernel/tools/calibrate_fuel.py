"""One-time (but re-runnable) offline calibration of `fuel_coef_s_per_kg`.

    uv run python python/f1_kernel/tools/calibrate_fuel.py            # 2023-2025, whatever exists
    uv run python python/f1_kernel/tools/calibrate_fuel.py --seasons 2024

Per CLAUDE.md's Phase 2 invariant ("no hardcoded magic numbers... all
CALIBRATED from historical data"), this is what actually produced
`KernelParams.fuel_coef_s_per_kg`'s default — see `f1_kernel.fuel.
calibrate_fuel_coefficient` for the method (a cross-circuit regression of
each stint's raw pace trend against that race's fuel burn rate; a
within-stint regression against estimated fuel load is not identified, see
`FuelStintEstimate`'s docstring for why).

This script's OUTPUT is what is committed, three ways:

1. `python/f1_kernel/calibration/fuel_coefficient.json` — the full record
   (value, uncertainty, sessions, method, every per-stint estimate) so the
   number is auditable without re-running anything.
2. `python/f1_kernel/reports/fuel_coefficient.md` — the human-readable
   version of the same run, part of the committed Phase 2 backtest suite
   `tools/run_backtest_suite.py` regenerates alongside the degradation and
   undercut reports.
3. `KernelParams.fuel_coef_s_per_kg`'s literal default in `params.py`, and
   its provenance flipped to `CALIBRATED` — done BY HAND after inspecting
   this script's output, the same way `tools/build_circuits.py`'s JSON is
   regenerated and reviewed rather than auto-applied.

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
from f1_kernel.fuel import FuelCalibrationResult, calibrate_fuel_coefficient
from f1_kernel.params import KernelParams
from f1_kernel.sources import HistoricalSource, list_historical_sessions

DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"
OUTPUT_PATH = Path(__file__).parents[1] / "calibration" / "fuel_coefficient.json"
REPORT_PATH = Path(__file__).parents[1] / "reports" / "fuel_coefficient.md"
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
    parser.add_argument("--min-stint-laps", type=int, default=5)
    parser.add_argument("--out", default=str(OUTPUT_PATH))
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

    print(f"calibrating over {len(sessions)} race(s): {sorted({s.year for s in sessions})}")
    result = calibrate_fuel_coefficient(
        source, sessions, KernelParams(), min_stint_laps=args.min_stint_laps
    )

    print(f"fuel_coef_s_per_kg = {result.fuel_coef_s_per_kg:.4f}")
    print(f"  95% CI: [{result.ci_low_s_per_kg:.4f}, {result.ci_high_s_per_kg:.4f}]")
    print(f"  from {result.n_stints} stint(s) across {result.n_sessions} session(s)")
    print(f"  compounds used: {', '.join(result.compounds_used)}")
    print(f"  method: {result.method}")

    document = {
        "fuel_coef_s_per_kg": result.fuel_coef_s_per_kg,
        "ci_low_s_per_kg": result.ci_low_s_per_kg,
        "ci_high_s_per_kg": result.ci_high_s_per_kg,
        "n_stints": result.n_stints,
        "n_sessions": result.n_sessions,
        "compounds_used": list(result.compounds_used),
        "method": result.method,
        "sessions": list(result.sessions),
        "min_stint_laps": args.min_stint_laps,
        "per_stint": [
            {
                "session_year": e.session_year,
                "session_round": e.session_round,
                "session_type": e.session_type,
                "driver": e.driver,
                "stint_number": e.stint_number,
                "compound": e.compound,
                "n_laps": e.n_laps,
                "burn_rate_kg_per_lap": e.burn_rate_kg_per_lap,
                "raw_slope_s_per_lap": e.raw_slope_s_per_lap,
            }
            for e in result.per_stint
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        _render_report(result, args.seasons, args.min_stint_laps), encoding="utf-8"
    )
    print(f"wrote {REPORT_PATH}")
    return 0


def _render_report(
    result: FuelCalibrationResult, seasons: Sequence[int], min_stint_laps: int
) -> str:
    lines = [
        "# Fuel coefficient calibration report",
        "",
        f"Generated by `python/f1_kernel/tools/calibrate_fuel.py` over {result.n_stints} "
        f"stint(s) across {result.n_sessions} session(s) (seasons requested: "
        f"{', '.join(str(s) for s in seasons)}; min stint laps: {min_stint_laps}).",
        "",
        "See `f1_kernel.fuel.FuelStintEstimate`'s docstring for why this is a cross-circuit "
        "regression of each stint's raw pace slope against that race's fuel burn rate, not a "
        "within-stint regression against estimated fuel load (the two are collinear there).",
        "",
        "## Result",
        "",
        f"- `fuel_coef_s_per_kg` = **{result.fuel_coef_s_per_kg:.4f}**",
        f"- 95% CI: [{result.ci_low_s_per_kg:.4f}, {result.ci_high_s_per_kg:.4f}]",
        f"- compounds used: {', '.join(result.compounds_used)}",
        "",
        "## Method",
        "",
        result.method,
        "",
        "## Sessions",
        "",
        *[f"- {session}" for session in result.sessions],
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
