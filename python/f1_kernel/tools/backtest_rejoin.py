"""The rejoin backtest (ROADMAP W5): for every real green-flag stop, the
"box now and it comes out P6, behind RUS" call made the lap before the
in-lap, scored against who was directly ahead at the end of the out-lap.

    uv run python python/f1_kernel/tools/backtest_rejoin.py --seasons 2024 \\
        --out python/f1_kernel/reports/rejoin_backtest_2024_dev.md
    uv run python python/f1_kernel/tools/backtest_rejoin.py --seasons 2025

Pit loss and its single-stop spread are measured on the seasons BEFORE the
one under test (`--pit-loss-seasons`, default: every earlier season in the
backfill), never on it: the shipped circuit files are calibrated on
2023-2025, so using them on 2025 would test the model on numbers it was
partly built from. 2024 is the development run (the confidence thresholds
are read there); 2025 is held out and run once those are settled.

Dev-only: needs `data/historical/` populated. Never imported at runtime.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import polars as pl

from f1_domain import SessionType
from f1_kernel.backtest import Backtest
from f1_kernel.frames import SessionFrames
from f1_kernel.params import KernelParams
from f1_kernel.pit_loss import MAD_TO_SIGMA, CircuitPitLoss, calibrate_pit_loss
from f1_kernel.rejoin import PitLossEstimate, evaluate_session_rejoins
from f1_kernel.sources import HistoricalSource, list_historical_sessions

DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"
OUTPUT_PATH = Path(__file__).parents[1] / "reports" / "rejoin_backtest.md"
DEFAULT_SEASONS = (2025,)
LEVELS = ("high", "medium", "low")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="historical backfill root")
    parser.add_argument("--seasons", type=int, nargs="+", default=list(DEFAULT_SEASONS))
    parser.add_argument(
        "--pit-loss-seasons",
        type=int,
        nargs="+",
        default=None,
        help="seasons pit loss is measured on (default: every season before --seasons)",
    )
    parser.add_argument("--min-stops", type=int, default=8, help="pit-loss floor per circuit")
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    args = parser.parse_args(argv[1:])

    source = HistoricalSource(args.root)
    races = [
        ref
        for ref in list_historical_sessions(args.root, seasons=args.seasons)
        if ref.session_key.session_type is SessionType.RACE
    ]
    if not races:
        print(f"no backfilled races for {args.seasons} under {args.root}", file=sys.stderr)
        return 1
    loss_seasons = args.pit_loss_seasons or [
        ref.session_key.year
        for ref in list_historical_sessions(args.root)
        if ref.session_key.year < min(args.seasons)
    ]
    loss_seasons = sorted(set(loss_seasons))
    if set(loss_seasons) & set(args.seasons):
        print("pit loss must not be measured on the seasons under test", file=sys.stderr)
        return 1
    loss_sessions = [
        ref.session_key
        for ref in list_historical_sessions(
            args.root, session_types=[SessionType.RACE], seasons=loss_seasons
        )
    ]
    if not loss_sessions:
        print(f"no races to measure pit loss on for {loss_seasons}", file=sys.stderr)
        return 1

    params = KernelParams()
    calibration = calibrate_pit_loss(
        source, loss_sessions, params, min_stops_per_circuit=args.min_stops
    )
    estimates = estimates_by_circuit(calibration.circuits)
    print(f"pit loss from {loss_seasons}: {len(estimates)} circuit(s) measured")

    labels = [f"{ref.label} ({ref.event_name})" for ref in races]
    result = Backtest(source, params=params).run(rows_with(estimates), races, raise_on_error=False)
    print(result.summary())
    report = render_report(labels, result.errors, result.table, loss_seasons, estimates)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


def estimates_by_circuit(circuits: Sequence[CircuitPitLoss]) -> dict[str, PitLossEstimate]:
    """Shipped circuit id -> its measured pit loss. A circuit under the
    sample floor keeps its median but no spread (low confidence)."""
    return {
        circuit.circuit_id: PitLossEstimate(
            value_s=circuit.pit_loss_s,
            sigma_s=None if circuit.thin else MAD_TO_SIGMA * circuit.mad_s,
            provenance="measured (thin)" if circuit.thin else "measured",
        )
        for circuit in circuits
        if circuit.circuit_id is not None
    }


def rows_with(
    estimates: Mapping[str, PitLossEstimate],
) -> Callable[[SessionFrames, KernelParams], list[dict[str, object]]]:
    """The `Backtest` analytic: one row per stop, pit loss from `estimates`."""

    def analytic(frames: SessionFrames, params: KernelParams) -> list[dict[str, object]]:
        circuit_id = frames.circuit.circuit_id if frames.circuit is not None else None
        estimate = estimates.get(circuit_id) if circuit_id is not None else None
        if estimate is None:
            estimate = PitLossEstimate(params.default_pit_loss_s, None, "default")
        rows: list[dict[str, object]] = []
        for o in evaluate_session_rejoins(frames, params, pit_loss=estimate):
            p = o.projection
            rows.append(
                {
                    "driver": o.driver,
                    "decision_lap": o.decision_lap,
                    "in_lap": o.in_lap,
                    "evaluated": o.evaluated,
                    "exclusion": o.exclusion,
                    "confidence": p.confidence.value,
                    "position_now": p.position_now,
                    "projected_position": p.position,
                    "projected_car_ahead": p.car_ahead,
                    "margin_s": p.margin_s,
                    "sigma_s": p.pit_loss_sigma_s,
                    "pit_loss_s": p.pit_loss_s,
                    "pit_loss_provenance": p.pit_loss_provenance,
                    "actual_position": o.actual_position,
                    "actual_car_ahead": o.actual_car_ahead,
                    "hit": o.hit,
                }
            )
        return rows

    return analytic


def wilson(hits: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    z = 1.959964
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return (centre - half, centre + half)


def _rates(scored: pl.DataFrame, column: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for value in scored[column].unique(maintain_order=True).sort(nulls_last=True).to_list():
        subset = scored.filter(
            pl.col(column).is_null() if value is None else pl.col(column) == value
        )
        out.append(
            ("n/a" if value is None else str(value), int(subset["hit"].sum()), subset.height)
        )
    return out


def _table(rows: list[tuple[str, int, int]], label: str) -> list[str]:
    lines = [f"| {label} | n | hit rate | 95% interval |", "| --- | --- | --- | --- |"]
    for value, hits, n in rows:
        low, high = wilson(hits, n)
        lines.append(f"| {value} | {n} | {hits / n:.1%} | {low:.1%} to {high:.1%} |")
    return lines


def ordering_verdict(scored: pl.DataFrame) -> str:
    rates = {
        level: hits / n
        for level, hits, n in _rates(scored, "confidence")
        if level in LEVELS and n > 0
    }
    if len(rates) < 3:
        return f"insufficient confidence spread to check ({sorted(rates)})"
    ordered = rates["high"] > rates["medium"] > rates["low"]
    return (
        f"**W5 {'PASS' if ordered else 'FAIL'}**: high={rates['high']:.1%} > "
        f"medium={rates['medium']:.1%} > low={rates['low']:.1%}"
    )


def render_report(
    labels: Sequence[str],
    errors: Sequence[object],
    table: pl.DataFrame,
    loss_seasons: Sequence[int],
    estimates: Mapping[str, PitLossEstimate],
) -> str:
    measured = sum(1 for e in estimates.values() if e.sigma_s is not None)
    lines = [
        "# Rejoin backtest report",
        "",
        f"Generated by `python/f1_kernel/tools/backtest_rejoin.py` over {len(labels)} race(s). "
        "Each call is made at the lap before the real in-lap, from line crossings through "
        "that lap only, and scored on who was directly ahead at the end of the out-lap. "
        f"Pit loss and its single-stop spread come from {', '.join(map(str, loss_seasons))} "
        f"only ({measured} circuit(s) measured, {len(estimates) - measured} under the "
        "sample floor), never from the season under test.",
        "",
        "## Corpus",
        "",
        *[f"- {label}" for label in labels],
        "",
    ]
    if errors:
        lines += ["## Session failures", "", *[f"- {e}" for e in errors], ""]
    if table.is_empty():
        return "\n".join([*lines, "_no stops found._", ""])

    scored = table.filter(pl.col("evaluated"))
    void = table.filter(~pl.col("evaluated"))
    lines += [
        "## Coverage",
        "",
        f"- Green-flag stops called: **{table.height}**",
        f"- Scored: **{scored.height}**",
        f"- Void (not a fair test, counted): **{void.height}**",
        "",
    ]
    if not void.is_empty():
        lines += ["| reason | count |", "| --- | --- |"]
        counts = void.group_by("exclusion").agg(pl.len().alias("n")).sort("n", descending=True)
        lines += [f"| {row['exclusion']} | {row['n']} |" for row in counts.to_dicts()]
        lines.append("")
    if scored.is_empty():
        return "\n".join(lines)

    hits = int(scored["hit"].sum())
    lines += [
        "## Hit rate",
        "",
        f"Overall: **{hits / scored.height:.1%}** of {scored.height} "
        "(right = the car directly ahead after the out-lap was the one projected).",
        "",
        "## W5 ordering check",
        "",
        "A call type ships on the live scorecard only when high beats medium beats low.",
        "",
        *_table(
            [r for level in LEVELS for r in _rates(scored, "confidence") if r[0] == level],
            "confidence",
        ),
        "",
        ordering_verdict(scored),
        "",
    ]
    with_sigma = scored.filter(pl.col("sigma_s").is_not_null() & pl.col("margin_s").is_not_null())
    if not with_sigma.is_empty():
        ratio = pl.col("margin_s") / pl.col("sigma_s")
        bucketed = with_sigma.with_columns(
            pl.when(ratio < 0.5)
            .then(pl.lit("a. under 0.5"))
            .when(ratio < 1)
            .then(pl.lit("b. 0.5 to 1"))
            .when(ratio < 2)
            .then(pl.lit("c. 1 to 2"))
            .when(ratio < 3)
            .then(pl.lit("d. 2 to 3"))
            .otherwise(pl.lit("e. 3 or more"))
            .alias("clear_air")
        )
        lines += [
            "## By clear air, in single-stop spreads",
            "",
            "How far the nearest car is from the rejoin point, divided by one stop's "
            "spread. The confidence thresholds sit at 1 and 2.",
            "",
            *_table(_rates(bucketed, "clear_air"), "clear air"),
            "",
        ]
    error = scored.with_columns(
        (pl.col("actual_position") - pl.col("projected_position")).alias("places_off")
    )
    counts = error.group_by("places_off").agg(pl.len().alias("n")).sort("places_off")
    lines += [
        "## Places off (actual minus projected position)",
        "",
        "| places off | n |",
        "| --- | --- |",
        *[f"| {row['places_off']:+d} | {row['n']} |" for row in counts.to_dicts()],
        "",
        "## Worst misses (high confidence, wrong)",
        "",
    ]
    misses = scored.filter((pl.col("confidence") == "high") & ~pl.col("hit"))
    if misses.is_empty():
        lines += ["_none._", ""]
    else:
        lines += [
            "| driver | in-lap | projected | actual | margin s | spread s |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in misses.sort("margin_s", descending=True).head(20).to_dicts():
            lines.append(
                f"| {row['driver']} | {row['in_lap']} | P{row['projected_position']} behind "
                f"{row['projected_car_ahead']} | P{row['actual_position']} behind "
                f"{row['actual_car_ahead']} | {row['margin_s']:.1f} | {row['sigma_s']:.1f} |"
            )
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
