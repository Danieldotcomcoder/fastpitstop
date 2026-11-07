"""Opt-in: mini-sectors from a real recorded race (the 2026 Dutch GP, as
the live worker recorded it). Skipped when the recording is absent — real
F1 data stays out of the repo (`data/` is gitignored).

What it pins, each found in this recording: the feed zeroes a lap's
mini-sectors in the message that completes the first one and leaves that
one's code out when unchanged (254 of 414 laps lost it); the last
mini-sector's code can arrive just after the lap count ticks (28 laps
credited it to the next lap); a race start resets with no tick at all.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from f1_domain import LapCompleted, MiniSectorCompleted, SessionKey, SessionType
from f1_parse import SessionStateMachine
from f1_recording.reader import RecordingReader

RECORDING = Path(__file__).resolve().parents[3] / "data" / "recordings" / "2026_12_dutch_race"
KEY = SessionKey(year=2026, round=12, session_type=SessionType.RACE)

pytestmark = pytest.mark.skipif(
    not RECORDING.is_dir(), reason=f"no recording at {RECORDING} (opt-in real data)"
)


def _replay() -> tuple[list[object], list[datetime]]:
    """Every event, and when the worker reconnected."""
    machine = SessionStateMachine(session_key=KEY)
    events: list[object] = []
    reconnects: list[datetime] = []
    for record in RecordingReader(RECORDING).records():
        if record.topic == "_meta.reconnect":
            reconnects.append(datetime.fromtimestamp(record.ts_wall, UTC))
        events.extend(machine.apply(record))
    return events, reconnects


def test_every_observed_lap_carries_each_of_its_mini_sectors() -> None:
    """Every lap the worker saw whole: not a car's first timed lap (the
    start), nor the lap the worker reconnected in (14:00:11, lap 20), whose
    changes during the gap are absorbed silently by design."""
    events, reconnects = _replay()
    minis = [e for e in events if isinstance(e, MiniSectorCompleted)]
    laps = [e for e in events if isinstance(e, LapCompleted)]
    assert {e.mini_sector_count for e in minis} == {24}, "Zandvoort's 2026 lap has 24"
    assert reconnects, "the recording's reconnect is what lap 20 is excluded for"

    seen: dict[tuple[str | None, int | None], set[int | None]] = defaultdict(set)
    for e in minis:
        seen[(e.driver, e.lap_number)].add(e.mini_sector)
    judged: list[tuple[str | None, int | None]] = []
    previous: dict[str | None, datetime] = {}
    for lap in laps:
        began = previous.get(lap.driver)
        previous[lap.driver] = lap.ts_utc
        if began is None or any(began < t <= lap.ts_utc for t in reconnects):
            continue
        judged.append((lap.driver, lap.lap_number))
    incomplete = [key for key in judged if seen.get(key) != set(range(24))]

    assert len(judged) > 300
    assert incomplete == []
