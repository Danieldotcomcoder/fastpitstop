"""Qualifying's structure rebuilt from history: the same Q-part, best-lap,
knockout and clock events the live feed's `TimingData`/`ExtrapolatedClock`
produce, so a backfilled qualifying replays through the same views.

What the backfill keeps, and what each event is built from:

- Parts: OpenF1's race control tags every message with its
  `qualifying_phase`. A part runs from its first green light (pit exit
  open) to its chequered flag; a red flag inside it stops the clock until
  the next green.
- The clock: the part's running time is the sum of its green-to-red and
  green-to-chequered stretches, so the time left at each restart is exact
  (the clock stops on a red flag, as F1's does). Emitted where the live
  clock starts, stops and ends.
- Best laps: every lap with a time, not deleted, that started while the
  part ran, emitted where it improves the driver's best in that part. Live,
  a deleted lap shows for a moment and is then withdrawn; history only
  knows the verdict, so it never shows.
- Knockouts: at the next part's green, as many cars as the format knocks
  out, from those with no time in the next part, slowest first (see
  `_emit_knockouts` for why not simply "outside the cut").
- Cut sizes: not in the history; the regulations knock out half the cars
  beyond ten in each of the first two parts (20 cars: 20, 15, 10; 22 cars:
  22, 16, 10), which is what the live feed's `NoEntries` reports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from f1_domain import (
    DomainEvent,
    QualifyingBestLap,
    QualifyingKnockout,
    QualifyingPartChanged,
    SessionClockUpdated,
)
from f1_historical_adapter.coerce import driver_number, opt_bool, opt_int, opt_timedelta, to_utc

if TYPE_CHECKING:
    from f1_historical_adapter.adapter import EnvFactory as Env

Add = Callable[[datetime, DomainEvent], None]

_PARTS = (1, 2, 3)


@dataclass(slots=True)
class Part:
    number: int
    greens: list[datetime] = field(default_factory=lambda: list[datetime]())
    reds: list[datetime] = field(default_factory=lambda: list[datetime]())
    chequered: datetime | None = None

    @property
    def start(self) -> datetime | None:
        return min(self.greens) if self.greens else None


def entries_for(cars: int) -> list[int]:
    """How many cars run in each part: everyone, then half of those beyond
    ten are knocked out in each of Q1 and Q2."""
    out = (cars - 10) // 2
    return [cars, cars - out, 10]


def parts_from_race_control(race_control: pd.DataFrame) -> list[Part]:
    """Each part's green lights, red flags and chequered flag, from the
    race-control messages tagged with their `qualifying_phase`."""
    if "qualifying_phase" not in race_control.columns:
        return []
    parts: dict[int, Part] = {}
    untagged_reds: list[datetime] = []
    for rec in race_control.to_dict("records"):
        ts = to_utc(rec.get("date"))
        if ts is None:
            continue
        flag = str(rec.get("flag") or "").upper()
        message = str(rec.get("message") or "").upper()
        phase = opt_int(rec.get("qualifying_phase"))
        is_red = flag == "RED" or message.startswith("RED FLAG")
        if phase is None:
            if is_red:
                untagged_reds.append(ts)
            continue
        if phase not in _PARTS:
            continue
        part = parts.setdefault(phase, Part(phase))
        if flag == "GREEN" or "GREEN LIGHT" in message:
            part.greens.append(ts)
        elif is_red:
            part.reds.append(ts)
        elif flag == "CHEQUERED" and part.chequered is None:
            part.chequered = ts
    ordered = [parts[n] for n in sorted(parts) if parts[n].start is not None]
    # A red flag with no phase on it belongs to the part it fell inside.
    for ts in untagged_reds:
        for part in ordered:
            if (
                part.start is not None
                and part.start < ts
                and (part.chequered is None or ts < part.chequered)
            ):
                part.reds.append(ts)
    return ordered


def _running_stretches(part: Part) -> list[tuple[datetime, datetime]]:
    """The stretches the part's clock ran: from each green to the next red
    flag, or to the chequered flag."""
    if part.chequered is None:
        return []
    marks = sorted(
        [(ts, "green") for ts in part.greens if ts < part.chequered]
        + [(ts, "red") for ts in part.reds if ts < part.chequered]
    )
    stretches: list[tuple[datetime, datetime]] = []
    running_since: datetime | None = None
    for ts, kind in marks:
        if kind == "green" and running_since is None:
            running_since = ts
        elif kind == "red" and running_since is not None:
            stretches.append((running_since, ts))
            running_since = None
    if running_since is not None:
        stretches.append((running_since, part.chequered))
    return stretches


def emit_qualifying(
    race_control: pd.DataFrame,
    laps: pd.DataFrame | None,
    results: pd.DataFrame | None,
    start: datetime | None,
    env: Env,
    add: Add,
) -> None:
    parts = parts_from_race_control(race_control)
    if not parts:
        return
    cars = len(results) if results is not None and not results.empty else 0
    entries = entries_for(cars) if cars > 10 else None

    for part in parts:
        assert part.start is not None
        add(part.start, QualifyingPartChanged(**env(part.start), part=part.number, entries=entries))
        stretches = _running_stretches(part)
        left = [sum((b - a for a, b in stretches[i:]), timedelta(0)) for i in range(len(stretches))]
        for (began, stopped), remaining in zip(stretches, left, strict=True):
            add(began, SessionClockUpdated(**env(began), remaining=remaining, running=True))
            after = remaining - (stopped - began)
            add(stopped, SessionClockUpdated(**env(stopped), remaining=after, running=False))

    if laps is not None and start is not None:
        _emit_best_laps(parts, laps, start, env, add)
    if results is not None and entries is not None:
        _emit_knockouts(parts, results, entries, env, add)


def _part_of(parts: list[Part], lap_start: datetime) -> Part | None:
    """The part a lap belongs to: the one running when it started."""
    for part in parts:
        if part.start is None or part.chequered is None:
            continue
        if part.start <= lap_start <= part.chequered:
            return part
    return None


def _emit_best_laps(
    parts: list[Part], laps: pd.DataFrame, start: datetime, env: Env, add: Add
) -> None:
    timed: list[tuple[datetime, str, int, int | None, timedelta]] = []
    for rec in laps.to_dict("records"):
        number = driver_number(rec.get("DriverNumber"))
        lap_time = opt_timedelta(rec.get("LapTime"))
        end = opt_timedelta(rec.get("Time"))
        if number is None or lap_time is None or end is None or opt_bool(rec.get("Deleted")):
            continue
        lap_start = opt_timedelta(rec.get("LapStartTime"))
        began = start + (lap_start if lap_start is not None else end - lap_time)
        part = _part_of(parts, began)
        if part is None:
            continue
        timed.append((start + end, number, part.number, opt_int(rec.get("LapNumber")), lap_time))
    best: dict[tuple[str, int], timedelta] = {}
    for ts, number, part, lap_number, lap_time in sorted(timed, key=lambda t: (t[0], t[1])):
        current = best.get((number, part))
        if current is not None and lap_time >= current:
            continue
        best[(number, part)] = lap_time
        add(
            ts,
            QualifyingBestLap(
                **env(ts), driver=number, part=part, lap_time=lap_time, lap_number=lap_number
            ),
        )


def _emit_knockouts(
    parts: list[Part], results: pd.DataFrame, entries: list[int], env: Env, add: Add
) -> None:
    by_number = {part.number: part for part in parts}
    still_in: set[str] = set()
    for rec in results.to_dict("records"):
        number = driver_number(rec.get("DriverNumber"))
        if number is not None:
            still_in.add(number)
    for part_number in (1, 2):
        following = by_number.get(part_number + 1)
        if following is None or following.start is None:
            return
        column, next_column = f"Q{part_number}", f"Q{part_number + 1}"
        # Out: the cars with no time in the next part, as many as the format
        # knocks out, slowest in this part first. Not "outside the cut on the
        # final times": a lap deleted after the part ended changes the final
        # order but not who was knocked out (Lindblad, 2026 Singapore SQ1).
        # And a car with no next-part time may still have gone through and
        # not run (Gasly and Hadjar, the same session's SQ3).
        no_next: list[tuple[timedelta, str]] = []
        for rec in results.to_dict("records"):
            number = driver_number(rec.get("DriverNumber"))
            if number is None or number not in still_in:
                continue
            if opt_timedelta(rec.get(next_column)) is None:
                lap = opt_timedelta(rec.get(column))
                no_next.append((lap if lap is not None else timedelta.max, number))
        knocked = max(0, len(still_in) - entries[part_number])
        slowest = sorted(no_next, key=lambda t: (t[0], len(t[1]), t[1]), reverse=True)[:knocked]
        out: list[str] = sorted((number for _, number in slowest), key=lambda n: (len(n), n))
        for number in out:
            add(
                following.start,
                QualifyingKnockout(**env(following.start), driver=number, part=part_number),
            )
        still_in -= set(out)
