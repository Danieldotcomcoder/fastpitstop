"""The degradation backtest CLAUDE.md asks for: run `fit_degradation` over
every backfilled race, check the directional and plausibility assertions
`tests/test_degradation.py` also encodes, and write the summary table as a
committed report.

    uv run python python/f1_kernel/tools/backtest_degradation.py
    uv run python python/f1_kernel/tools/backtest_degradation.py --seasons 2024

This is reporting, not a calibration: nothing here is written back into
`KernelParams` (that is `calibrate_compound_offsets.py`, for the offsets
`fit_degradation`'s own intercepts feed into). Re-running it never changes
the kernel's behaviour, only `python/f1_kernel/reports/degradation_backtest.md`.

Dev-only: needs `data/historical/` populated. Never imported at runtime.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

import polars as pl

from f1_domain import SessionKey, SessionType
from f1_kernel.cleaning import clean_laps
from f1_kernel.degradation import (
    DegradationFit,
    Reliability,
    fit_degradation,
    summarize_degradation_by_compound,
)
from f1_kernel.fuel import fuel_correct
from f1_kernel.load import lap_frame
from f1_kernel.params import KernelParams
from f1_kernel.sources import HistoricalSource, list_historical_sessions

DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"
OUTPUT_PATH = Path(__file__).parents[1] / "reports" / "degradation_backtest.md"
DEFAULT_SEASONS = (2023, 2024, 2025)

PLAUSIBLE_LOW_S_PER_LAP = -0.3
PLAUSIBLE_HIGH_S_PER_LAP = 1.5
MAX_OUTLIER_FRACTION = 0.05


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

    params = KernelParams()
    print(f"fitting degradation over {len(sessions)} race(s): {sorted({s.year for s in sessions})}")

    fits: list[DegradationFit] = []
    failed = 0
    for key in sessions:
        try:
            laps = clean_laps(lap_frame(source, key), params)
            if laps.is_empty():
                continue
            fits.extend(fit_degradation(fuel_correct(laps, params), params))
        except Exception as exc:  # one bad session must not end the run
            failed += 1
            print(f"  skipped {key.year} R{key.round:02d}: {type(exc).__name__}: {exc}")

    print(f"collected {len(fits)} fit(s) ({failed} session(s) failed)")

    summary = summarize_degradation_by_compound(fits)
    outliers = [
        fit
        for fit in fits
        if fit.reliability is not Reliability.THIN
        and not (PLAUSIBLE_LOW_S_PER_LAP <= fit.deg_rate_s_per_lap <= PLAUSIBLE_HIGH_S_PER_LAP)
    ]
    checked = [fit for fit in fits if fit.reliability is not Reliability.THIN]
    outlier_fraction = len(outliers) / len(checked) if checked else 0.0

    by_compound = {row["compound"]: row["median_deg_rate_s_per_lap"] for row in summary.to_dicts()}
    softer_faster = _check_directional_sanity(by_compound)

    print("\n" + _frame_to_markdown(summary))
    print(
        f"\noutliers outside [{PLAUSIBLE_LOW_S_PER_LAP}, {PLAUSIBLE_HIGH_S_PER_LAP}] s/lap: "
        f"{len(outliers)}/{len(checked)} ({outlier_fraction:.1%})"
    )
    print(f"directional sanity (softer compounds degrade faster, median): {softer_faster}")

    report = _render_report(sessions, fits, summary, outliers, softer_faster, outlier_fraction)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nwrote {out_path}")

    return 0 if outlier_fraction <= MAX_OUTLIER_FRACTION else 1


def _check_directional_sanity(by_compound: dict[str, float]) -> str:
    order = [c for c in ("soft", "medium", "hard") if c in by_compound]
    if len(order) < 2:
        return "insufficient compounds to check"
    values = [by_compound[c] for c in order]
    return "PASS" if all(a > b for a, b in pairwise(values)) else "FAIL"


def _render_report(
    sessions: Sequence[SessionKey],
    fits: list[DegradationFit],
    summary: pl.DataFrame,
    outliers: list[DegradationFit],
    softer_faster: str,
    outlier_fraction: float,
) -> str:
    lines = [
        "# Degradation backtest report",
        "",
        f"Generated by `python/f1_kernel/tools/backtest_degradation.py` over "
        f"{len(sessions)} race(s), producing {len(fits)} `DegradationFit`(s).",
        "",
        "## Summary by compound",
        "",
        _frame_to_markdown(summary),
        "",
        "## Sanity checks",
        "",
        f"- Directional (softer compounds degrade faster, by median): **{softer_faster}**",
        f"- Outliers outside [{PLAUSIBLE_LOW_S_PER_LAP}, {PLAUSIBLE_HIGH_S_PER_LAP}] s/lap: "
        f"{len(outliers)} ({outlier_fraction:.1%}) — "
        f"{'within' if outlier_fraction <= MAX_OUTLIER_FRACTION else 'ABOVE'} the "
        f"{MAX_OUTLIER_FRACTION:.0%} tolerance",
        "",
    ]
    if outliers:
        lines += ["## Flagged outliers (for inspection, not excluded)", ""]
        lines.append(
            "| session | driver | stint | compound | deg_rate_s_per_lap | n_laps | reliability |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for fit in sorted(outliers, key=lambda f: abs(f.deg_rate_s_per_lap), reverse=True)[:30]:
            lines.append(
                f"| {fit.session_year} R{fit.session_round:02d} {fit.session_type} "
                f"| {fit.driver} | {fit.stint_number} | {fit.compound} "
                f"| {fit.deg_rate_s_per_lap:.3f} | {fit.n_laps_used} | {fit.reliability.value} |"
            )
        lines.append("")
    return "\n".join(lines)


def _frame_to_markdown(frame: pl.DataFrame) -> str:
    if frame.is_empty():
        return "_no fits produced_"
    columns = frame.columns
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.to_dicts():
        formatted = [
            f"{value:.4f}" if isinstance(value, float) else str(value) for value in row.values()
        ]
        lines.append("| " + " | ".join(formatted) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
