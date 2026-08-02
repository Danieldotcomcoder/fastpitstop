"""One-time (but re-runnable) offline calibration of `compound_offset_s`.

    uv run python python/f1_kernel/tools/calibrate_compound_offsets.py
    uv run python python/f1_kernel/tools/calibrate_compound_offsets.py --seasons 2024

See `f1_kernel.degradation.calibrate_compound_offsets` for the method: for
every driver who ran the reference compound (soft) and another dry compound
in the SAME session, pair their fuel-corrected fresh-tyre baseline paces
(`DegradationFit.intercept_s`) into one delta, then take the median delta
per compound across the whole corpus.

Unlike `calibrate_fuel.py`, this script's number is NOT applied
automatically — read `calibrate_compound_offsets`' docstring for why: the
pairing does not fully cancel session-level drift (track evolution,
temperature, traffic) the way it cancels within one stint, so a compound
whose fitted sign or magnitude looks physically implausible against public
tyre charts (hard slower than medium slower than soft) should be left at
its previous PROVISIONAL default rather than written in. This script prints
that judgement call explicitly; it does not make it for you.

This script's OUTPUT is what is committed, twice over:

1. `python/f1_kernel/calibration/compound_offsets.json` — the full record
   (values, sessions, method, every per-pair sample) so the numbers are
   auditable without re-running anything.
2. `KernelParams.compound_offset_s`'s literal defaults in `params.py` for
   whichever compounds pass the plausibility check — done BY HAND after
   inspecting this script's output, same as `calibrate_fuel.py`.

Dev-only: needs `data/historical/` populated (`uv run f1-backfill --season
2024`, ...). Never imported by the kernel at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path

from f1_domain import SessionType
from f1_kernel.degradation import calibrate_compound_offsets
from f1_kernel.params import KernelParams
from f1_kernel.sources import HistoricalSource, list_historical_sessions

DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"
OUTPUT_PATH = Path(__file__).parents[1] / "calibration" / "compound_offsets.json"
DEFAULT_SEASONS = (2023, 2024, 2025)

PLAUSIBLE_ORDER = ("soft", "medium", "hard")


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
    result = calibrate_compound_offsets(source, sessions, KernelParams())

    print(f"compound_offset_s = {result.compound_offset_s}")
    print(f"  reference compound: {result.reference_compound}")
    print(f"  from {result.n_samples} paired sample(s) across {result.n_sessions} session(s)")
    print(f"  method: {result.method}")

    ordered = [c for c in PLAUSIBLE_ORDER if c in result.compound_offset_s]
    values = [result.compound_offset_s[c] for c in ordered]
    is_monotonic = all(a <= b for a, b in pairwise(values))
    print(f"  plausibility check (soft <= medium <= hard): {'PASS' if is_monotonic else 'FAIL'}")
    if not is_monotonic:
        print(
            "  -> at least one compound's fitted offset is out of the expected order; "
            "per this script's own docstring, do NOT write that compound's number into "
            "KernelParams.compound_offset_s — keep its existing PROVISIONAL default and "
            "note the finding in docs/PHASE2.md instead"
        )

    document = {
        "compound_offset_s": result.compound_offset_s,
        "reference_compound": result.reference_compound,
        "n_samples": result.n_samples,
        "n_sessions": result.n_sessions,
        "compounds_calibrated": list(result.compounds_calibrated),
        "method": result.method,
        "sessions": list(result.sessions),
        "plausibility_check_passed": is_monotonic,
        "samples": [
            {
                "session_year": s.session_year,
                "session_round": s.session_round,
                "session_type": s.session_type,
                "driver": s.driver,
                "compound": s.compound,
                "reference_compound": s.reference_compound,
                "delta_s": s.delta_s,
                "n_laps": s.n_laps,
            }
            for s in result.samples
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
