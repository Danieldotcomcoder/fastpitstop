"""The Phase 2 backtest suite: refresh every committed report in one command.

    uv run python python/f1_kernel/tools/run_backtest_suite.py
    uv run python python/f1_kernel/tools/run_backtest_suite.py --seasons 2024 2025

CLAUDE.md's Prompt 2.7 asks for "a full backtest suite over ALL historical
sessions producing committed reports: degradation summaries, fuel
coefficient, per-circuit pit loss, and the undercut hit/miss scorecard."
This is that suite — one command instead of four, running every
calibration/backtest tool this package already ships over the same corpus
and refreshing every committed artifact in one pass:

- `calibrate_fuel.py`       -> calibration/fuel_coefficient.json + reports/fuel_coefficient.md
- `calibrate_pit_loss.py`   -> calibration/pit_loss.json + reports/pit_loss.md
- `backtest_degradation.py` -> reports/degradation_backtest.md
- `backtest_undercut.py`    -> reports/undercut_backtest.md

It is deliberately just an orchestrator — each step is the same
independently runnable, independently tested tool it always was; nothing
here recomputes an analytic itself, and nothing here writes into
`KernelParams` or the shipped `circuits/*.json` automatically. Every
calibrated number still goes through the by-hand review step each
individual tool's own docstring describes (`--write-circuits` on
`calibrate_pit_loss.py` is intentionally NOT wired in here for the same
reason). `just verify-phase2` is the other half of this deliverable: it
asserts the reports this script writes still say what they said last time,
and runs with no data at all — see `f1_kernel.verify`.

`--seasons`, when given, is forwarded to every step. Left unset (the
default), each step keeps its OWN default seasons — 2023-2025 for
fuel/pit-loss/degradation, but 2025 ONLY for the undercut backtest, since
that one is deliberately a held-out validation season no parameter was
calibrated on (see `tools/backtest_undercut.py`). Forcing one uniform
season list here would quietly erase that distinction.

One step failing (e.g. a season with no qualifying stints for the fuel
regression) does not stop the others — the same "one bad thing does not
end the run" discipline `f1_kernel.Backtest` follows — and the exit code
is nonzero if ANY step failed.

Dev-only: needs `data/historical/` populated (`uv run f1-backfill --season
<year>`, ...). Never imported by the kernel at runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOOLS_DIR = Path(__file__).parent
DEFAULT_ROOT = Path(__file__).parents[3] / "data" / "historical"

STEPS: tuple[tuple[str, str], ...] = (
    ("calibrate_fuel", "fuel coefficient calibration"),
    ("calibrate_pit_loss", "per-circuit pit-loss calibration"),
    ("backtest_degradation", "degradation directional-sanity backtest"),
    ("backtest_undercut", "undercut/overcut calibration backtest"),
)


def _load(module_name: str) -> ModuleType:
    """Import a sibling `tools/*.py` script by path — `tools/` is a
    directory of standalone scripts (each run via `uv run python
    tools/foo.py`), not an installed package, so this is the one way to
    call another one's `main()` in-process rather than shelling out."""
    spec = importlib.util.spec_from_file_location(module_name, TOOLS_DIR / f"{module_name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load tools/{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="historical backfill root")
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=None,
        help=(
            "seasons to forward to every step (default: each tool's own default — "
            "see the module docstring for why that differs for the undercut backtest)"
        ),
    )
    args = parser.parse_args(argv[1:])

    results: list[tuple[str, int]] = []
    for module_name, label in STEPS:
        banner = f"{label} ({module_name}.py)"
        print(f"\n{'=' * len(banner)}\n{banner}\n{'=' * len(banner)}")
        step_argv = [module_name, "--root", args.root]
        if args.seasons is not None:
            step_argv += ["--seasons", *(str(season) for season in args.seasons)]
        try:
            code = _load(module_name).main(step_argv)
        except Exception as exc:  # one step's failure must not end the suite
            print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
            code = 1
        results.append((label, code))
        print(f"-- {label}: {'OK' if code == 0 else f'FAILED (exit {code})'}")

    overall = max((code for _label, code in results), default=0)
    print(f"\n{'=' * 22}\nBACKTEST SUITE SUMMARY\n{'=' * 22}")
    for label, code in results:
        print(f"  [{'OK' if code == 0 else 'FAIL'}] {label}")
    print("\nBACKTEST SUITE: " + ("OK" if overall == 0 else "FAILED — see above"))
    return overall


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
