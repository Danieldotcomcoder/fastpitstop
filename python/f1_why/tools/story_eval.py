"""Story evaluation: is every card the story shows true, are the race's
headline moments in it, and is every number in every sentence real?

Runs the story exactly as `f1_api` builds it (`analyze_session`, the grid
from the event stream, `detect_anomalies`, `select_story`, evidence, the
template narrator) over backfilled races, then checks it three ways:

1. **Precision.** Each card is judged against the RAW FastF1 tables
   (`laps`, `results`, `race_control` parquet), never against the frames
   the detector itself read: an overtake must show the cars swapping order
   with the passed car not pitting and the lap not neutralized; a safety
   car must be one race control deployed; a retirement must be one the
   results record; and so on (`judge`).
2. **Recall.** `tests/eval/headlines.json` lists each race's headline
   moments, written by hand and each verified against that race's race
   control or results before being added. A headline is found when a card
   of its kind names its drivers within two laps of it.
3. **Facts.** Every number in every sentence must be a value in the card's
   own evidence bundle (or a lap or position number).

Usage (needs the backfill under data/historical/):

    uv run python python/f1_why/tools/story_eval.py
    uv run python python/f1_why/tools/story_eval.py --races 2024-6,2024-21

Writes `python/f1_why/reports/story_eval.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from f1_domain import SessionKey, SessionType
from f1_kernel import KernelParams, analyze_session
from f1_kernel.sources import HistoricalSource
from f1_why import (
    Anomaly,
    AnomalyKind,
    AnomalyParams,
    EvidenceBundle,
    EvidenceParams,
    EvidenceSources,
    NarrationParams,
    TemplateNarrationModel,
    assemble_evidence,
    detect_anomalies,
    explain,
    select_story,
    starting_grid,
)

REPO = Path(__file__).resolve().parents[3]
HISTORICAL = REPO / "data" / "historical"
HEADLINES = REPO / "python" / "f1_why" / "tests" / "eval" / "headlines.json"
REPORT = REPO / "python" / "f1_why" / "reports" / "story_eval.md"

DEFAULT_RACES = (
    "2024-1",
    "2024-3",
    "2024-6",
    "2024-8",
    "2024-9",
    "2024-11",
    "2024-13",
    "2024-14",
    "2024-21",
    "2025-9",
)
_NEUTRAL_CODES = frozenset("4567")
"""FastF1 TrackStatus digits for a safety car (4), red flag (5) and VSC (6, 7)."""
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Raw:
    """One race's raw FastF1 tables, indexed for the judge."""

    laps: dict[tuple[str, int], dict[str, object]]
    results: dict[str, dict[str, object]]
    race_control: list[dict[str, object]]
    final_lap: int

    @classmethod
    def load(cls, year: int, round_: int) -> Raw:
        base = HISTORICAL / f"season={year}" / f"round={round_:02d}" / "session=Race"
        laps = pl.read_parquet(base / "laps.parquet")
        results = pl.read_parquet(base / "results.parquet")
        control = pl.read_parquet(base / "race_control.parquet")
        indexed = {
            (str(row["DriverNumber"]), int(row["LapNumber"])): row
            for row in laps.to_dicts()
            if row["LapNumber"] is not None
        }
        return cls(
            laps=indexed,
            results={str(row["DriverNumber"]): row for row in results.to_dicts()},
            race_control=control.to_dicts(),
            final_lap=max(lap for _, lap in indexed),
        )

    def position(self, driver: str, lap: int) -> int | None:
        row = self.laps.get((driver, lap))
        value = row.get("Position") if row else None
        return int(value) if isinstance(value, int | float) and value == value else None

    def pitting(self, driver: str, lap: int) -> bool:
        row = self.laps.get((driver, lap))
        return bool(row) and (row.get("PitInTime") is not None or row.get("PitOutTime") is not None)

    def neutralized(self, driver: str, lap: int) -> bool:
        row = self.laps.get((driver, lap))
        status = str(row.get("TrackStatus") or "") if row else ""
        return any(code in _NEUTRAL_CODES for code in status)

    def lap_s(self, driver: str, lap: int) -> float | None:
        row = self.laps.get((driver, lap))
        value = row.get("LapTime") if row else None
        return value.total_seconds() if value is not None else None  # type: ignore[union-attr]


def _int(anomaly: Anomaly, key: str) -> int | None:
    value = anomaly.summary.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


_DEPLOYED = {"sc": "SAFETY CAR DEPLOYED", "vsc": "VIRTUAL SAFETY CAR", "red": "RED FLAG"}


def _deployed(raw: Raw, status: str, near: int) -> bool:
    """Race control deployed `status` within a lap of `near`."""
    phrase = _DEPLOYED[status]
    for row in raw.race_control:
        text = str(row.get("message") or "").upper()
        lap_number = row.get("lap_number")
        if not isinstance(lap_number, int | float) or abs(int(lap_number) - near) > 1:
            continue
        if phrase in text and not (status == "sc" and "VIRTUAL" in text):
            return True
    return False


def _swapped(raw: Raw, gainer: str, other: str, lap: int) -> bool:
    before = (raw.position(gainer, lap - 1), raw.position(other, lap - 1))
    after = (raw.position(gainer, lap), raw.position(other, lap))
    if None in before or None in after:
        return False
    return before[0] > before[1] and after[0] < after[1]  # type: ignore[operator]


def judge(card: Anomaly, raw: Raw) -> str | None:
    """`None` when the raw tables bear the card out, else why not."""
    kind, lap, s = card.kind, card.lap_number or 0, card.summary
    if kind is AnomalyKind.POSITION_CHANGE:
        driver = card.drivers[0]
        if s.get("gained") is False:
            passers = str(s.get("passed_by") or "").split(",")
            if raw.pitting(driver, lap):
                return "the car that dropped back was pitting"
            if not all(_swapped(raw, other, driver, lap) for other in passers if other):
                return "raw positions do not show those cars passing it"
            return None
        passed = [p for p in str(s.get("passed") or "").split(",") if p]
        if raw.neutralized(driver, lap):
            return "lap was neutralized"
        if raw.pitting(driver, lap) or raw.pitting(driver, lap - 1):
            return "the passing car was pitting"
        for other in passed:
            if raw.pitting(other, lap):
                return f"car {other} was pitting, not overtaken"
            if not _swapped(raw, driver, other, lap):
                return f"raw positions do not show a swap with car {other}"
        return None
    if kind is AnomalyKind.UNDERCUT_RESOLVED:
        attacker, defender = card.drivers[0], card.drivers[1]
        a_stop, d_stop = _int(card, "attacker_stop_lap"), _int(card, "defender_stop_lap")
        if a_stop is None or d_stop is None:
            return "stop laps missing"
        if not raw.pitting(attacker, a_stop) and not raw.pitting(attacker, a_stop + 1):
            return "no raw stop for the attacker on that lap"
        if not raw.pitting(defender, d_stop) and not raw.pitting(defender, d_stop + 1):
            return "no raw stop for the defender on that lap"
        ahead = raw.position(attacker, lap)
        behind = raw.position(defender, lap)
        if ahead is None or behind is None:
            return "no raw positions at the outcome lap"
        if (ahead < behind) != bool(s.get("order_changed")):
            return "raw order at the outcome lap disagrees"
        return None
    if kind is AnomalyKind.NEUTRALIZATION:
        start = _int(card, "start_lap") or lap
        opening = str(s.get("opening_status") or s.get("status"))
        worst_from = _int(card, "worst_from_lap") or start
        for status, near in ((opening, start), (str(s.get("status")), worst_from)):
            if not _deployed(raw, status, near):
                return f"race control deployed no {status} near lap {near}"
        return None
    if kind is AnomalyKind.RETIREMENT:
        driver = card.drivers[0]
        row = raw.results.get(driver)
        if row is None:
            return "driver not in results"
        status = str(row.get("Status") or "")
        laps = row.get("Laps")
        # A car that retired after 90% distance is classified, and FastF1
        # then calls it "Lapped": the 2024 Austrian GP's Norris, out on lap
        # 65 of 71. Several laps short is a retirement whatever the label.
        short = isinstance(laps, int | float) and int(laps) < raw.final_lap - 3
        if status == "Finished" or ((status == "Lapped" or status.startswith("+")) and not short):
            return f"results say {status!r}"
        laps = row.get("Laps")
        last = _int(card, "last_completed_lap") or 0
        if isinstance(laps, int | float) and abs(int(laps) - last) > 1:
            return f"results record {int(laps)} laps, the card {last}"
        return None
    if kind is AnomalyKind.RACE_START:
        leaders = [d for (d, n), _ in raw.laps.items() if n == 1 and raw.position(d, 1) == 1]
        pole = [d for d, r in raw.results.items() if r.get("GridPosition") == 1]
        if leaders and s.get("leader") != leaders[0]:
            return f"raw lap-1 leader is {leaders[0]}"
        if pole and s.get("pole") != pole[0]:
            return f"results pole-sitter is {pole[0]}"
        return None
    if kind is AnomalyKind.RACE_FINISH:
        winner = [d for (d, n), _ in raw.laps.items() if n == lap and raw.position(d, lap) == 1]
        if not winner or winner[0] != s.get("winner"):
            return "raw final-lap leader disagrees"
        return None
    if kind is AnomalyKind.PIT_ANOMALY:
        driver = card.drivers[0]
        in_lap, out_lap = _int(card, "in_lap") or lap, _int(card, "out_lap") or lap + 1
        if not raw.pitting(driver, in_lap) and not raw.pitting(driver, out_lap):
            return "no raw stop on those laps"
        return None
    if kind in (AnomalyKind.PACE_LOSS, AnomalyKind.OFF_TRACK, AnomalyKind.DEG_CLIFF):
        driver = card.drivers[0]
        this = raw.lap_s(driver, lap)
        earlier = [
            t
            for n in range(lap - 8, lap - 2)
            if (t := raw.lap_s(driver, n)) is not None
            and not raw.pitting(driver, n)
            and not raw.neutralized(driver, n)
        ]
        if this is None or len(earlier) < 3:
            return "too few raw laps to check"
        earlier.sort()
        median = earlier[len(earlier) // 2]
        # Raw lap times are not fuel-corrected: a later lap is a little
        # quicker for its lighter car, so this is a lenient floor.
        if this - median < 0.4 * card.magnitude:
            return f"raw lap is only {this - median:.1f}s off, the card {card.magnitude:.1f}s"
        return None
    return None


def fact_errors(text: str, bundle: EvidenceBundle, final_lap: int) -> list[str]:
    known = {str(n) for n in range(final_lap + 25)}
    for item in bundle.items:
        for value in item.data.values():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            known.update({str(value), f"{value:.1f}", str(round(value)), f"{abs(value):.1f}"})
    return [n for n in _NUMBER.findall(text) if n not in known]


@dataclass
class RaceResult:
    race: str
    event: str | None
    cards: list[tuple[Anomaly, str, str | None, list[str]]] = field(
        default_factory=list[tuple[Anomaly, str, str | None, list[str]]]
    )
    found: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    missed: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


def evaluate(race: str, headlines: list[dict[str, object]]) -> RaceResult:
    year, round_ = (int(part) for part in race.split("-"))
    key = SessionKey(year=year, round=round_, session_type=SessionType.RACE)
    source = HistoricalSource(HISTORICAL)
    report = analyze_session(source, key, KernelParams())
    grid = starting_grid(source.events(key, kinds=["position_changed"]), report.frames.laps)
    anomalies = detect_anomalies(report.frames, report, AnomalyParams(), grid=grid)
    events = tuple(source.events(key, kinds=["race_control_message", "driver_list_updated"]))
    sources = EvidenceSources(session_frames=report.frames, kernel_report=report, events=events)
    raw = Raw.load(year, round_)

    result = RaceResult(race=race, event=report.event_name)
    for card in select_story(anomalies):
        bundle = assemble_evidence(card, sources, EvidenceParams())
        explanation = explain(bundle, TemplateNarrationModel(), NarrationParams())
        if not explanation.grounded:
            continue  # f1_api.why.worth_showing: no card
        verdict = judge(card, raw)
        result.cards.append(
            (card, explanation.text, verdict, fact_errors(explanation.text, bundle, raw.final_lap))
        )

    for headline in headlines:
        lap = int(headline["lap"])  # type: ignore[arg-type]
        drivers = {str(d) for d in headline.get("drivers", [])}  # type: ignore[union-attr]
        hit = any(
            card.kind.value == headline["kind"]
            and card.lap_number is not None
            and abs(card.lap_number - lap) <= 2
            and drivers <= set(card.drivers)
            for card, _, _, _ in result.cards
        )
        (result.found if hit else result.missed).append(headline)
    return result


def render(results: list[RaceResult]) -> str:
    cards = sum(len(r.cards) for r in results)
    true = sum(1 for r in results for c in r.cards if c[2] is None)
    facts = sum(len(c[3]) for r in results for c in r.cards)
    found = sum(len(r.found) for r in results)
    headlines = found + sum(len(r.missed) for r in results)
    lines = [
        "# Story evaluation report",
        "",
        "Generated by `python/f1_why/tools/story_eval.py` — see its docstring for the method.",
        "",
        "## Summary",
        "",
        "| races | cards | cards borne out by raw data | headline moments found "
        "| numbers not in the evidence |",
        "| --- | --- | --- | --- | --- |",
        f"| {len(results)} | {cards} | {true}/{cards} ({100 * true / max(cards, 1):.0f}%) | "
        f"{found}/{headlines} ({100 * found / max(headlines, 1):.0f}%) | {facts} |",
        "",
        "Targets (docs/ROADMAP.md, W1): precision at least 90%, recall at least 80%, "
        "no unsupported numbers.",
        "",
        "Before Story 2.0 (measured 2026-10-11 with this judge against git `04d2179`, the same",
        "ten races, its twelve most salient moments each): 120 cards, 47 borne out by the raw",
        "data (39%), 29 of them overtakes that were the pit lane reshuffling the order, 24",
        'pit-stop "duels" in which nothing changed, and 2 of the 35 headline moments found (6%).',
        "",
    ]
    for result in results:
        lines += [f"## {result.race} — {result.event}", ""]
        for card, text, verdict, errors in result.cards:
            mark = "✓" if verdict is None and not errors else "✗"
            note = f" — **{verdict}**" if verdict else ""
            note += f" — **unsupported numbers: {', '.join(errors)}**" if errors else ""
            lines.append(f"- {mark} L{card.lap_number} `{card.kind.value}` {text}{note}")
        if result.missed:
            lines += ["", "Headlines missed:"]
            lines += [f"- L{h['lap']} {h['kind']}: {h.get('note', '')}" for h in result.missed]
        lines.append("")
    return "\n".join(lines)


TARGET_PRECISION = 0.90
TARGET_RECALL = 0.80
"""docs/ROADMAP.md W1's targets, which `--check` (the release gate,
`just verify-real`) enforces."""


def shortfalls(results: list[RaceResult]) -> list[str]:
    """What keeps `results` short of the targets; empty when they are met."""
    cards = [card for result in results for card in result.cards]
    true = sum(1 for card in cards if card[2] is None)
    found = sum(len(result.found) for result in results)
    headlines = found + sum(len(result.missed) for result in results)
    facts = sum(len(card[3]) for card in cards)
    problems: list[str] = []
    if cards and true / len(cards) < TARGET_PRECISION:
        problems.append(f"precision {true}/{len(cards)} is under {TARGET_PRECISION:.0%}")
    if headlines and found / headlines < TARGET_RECALL:
        problems.append(f"recall {found}/{headlines} is under {TARGET_RECALL:.0%}")
    if facts:
        problems.append(f"{facts} number(s) in sentences are not in their evidence")
    for result in results:
        for anomaly, _, verdict, _ in result.cards:
            if verdict is not None:
                problems.append(
                    f"{result.race} L{anomaly.lap_number} {anomaly.kind.value}: {verdict}"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--races", default=",".join(DEFAULT_RACES))
    parser.add_argument("--out", type=Path, default=REPORT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="release-gate mode: write no report, exit 1 when a target is missed",
    )
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore")
    headlines: dict[str, list[dict[str, object]]] = json.loads(
        HEADLINES.read_text(encoding="utf-8")
    )
    results: list[RaceResult] = []
    for race in args.races.split(","):
        if not (HISTORICAL / f"season={race.split('-')[0]}").exists():
            print(f"skipping {race}: not in the backfill", file=sys.stderr)
            continue
        print(f"evaluating {race}", file=sys.stderr)
        results.append(evaluate(race, headlines.get(race, [])))
    if args.check:
        if not results:
            print("story check: SKIPPED (no backfilled race to evaluate)")
            return 0
        problems = shortfalls(results)
        cards = sum(len(result.cards) for result in results)
        if problems:
            print(f"story check: FAIL over {len(results)} races, {cards} cards")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"story check: OK over {len(results)} races, {cards} cards, targets met")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results), encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
