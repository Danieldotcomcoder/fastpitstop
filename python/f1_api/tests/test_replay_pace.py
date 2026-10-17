"""Changing a replay's speed without restarting it: `ReplayPace` re-anchors
the replay line at the current moment of the race, and the WebSocket's
`{"type": "speed"}` message drives it. Against the checked-in fixture race,
with a fake clock, so nothing here really waits."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from api_fixture import HISTORICAL_ROOT, SESSION_KEY

from f1_api.stream.replay import ReplayPace, replay_envelopes
from f1_api.stream.websocket import replay_speed_from
from f1_domain import SessionKey
from f1_kernel import HistoricalSource
from f1_replay import pacing_offset_s

FAKE_WALL_START = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _key(path: str) -> SessionKey:
    year, rnd, kind = path.split("-")
    return SessionKey.model_validate({"year": int(year), "round": int(rnd), "session_type": kind})


def test_until_a_change_it_is_the_replay_harness_timing_model() -> None:
    pace = ReplayPace(4.0, delay=2.0)
    for elapsed in (0.0, 10.0, 125.5):
        assert pace.offset(elapsed) == pacing_offset_s(elapsed, speed=4.0, delay=2.0)


def test_a_new_speed_carries_on_from_the_moment_reached() -> None:
    pace = ReplayPace(1.0)
    clock = _FakeClock()
    pace.begin(0.0, clock.now)
    # At 1x, 10 s into the replay is 10 s into the race; then 4x from there.
    assert pace.wait_for(10.0, start_wall=0.0, clock=clock.now, sleep=clock.sleep) == 10.0
    pace.set_speed(4.0)
    assert pace.offset(30.0) == pytest.approx(10.0 + 20.0 / 4.0)
    # And back to 1x at wall 15 s (race 30 s).
    clock.value = 15.0
    pace.set_speed(1.0)
    assert pace.offset(40.0) == pytest.approx(15.0 + 10.0)


def test_offsets_never_go_backwards_when_a_change_lands_while_running_late() -> None:
    pace = ReplayPace(16.0)
    clock = _FakeClock()
    pace.begin(0.0, clock.now)
    first = pace.wait_for(160.0, start_wall=0.0, clock=clock.now, sleep=clock.sleep)  # 10 s
    # Slowing right down with the wall clock still at 10 s, from moment 160.
    pace.set_speed(1.0)
    second = pace.wait_for(150.0, start_wall=0.0, clock=clock.now, sleep=clock.sleep)
    assert second >= first


def test_a_speed_change_wakes_a_waiting_replay() -> None:
    pace = ReplayPace(1.0)
    start = time.monotonic()
    pace.begin(start, time.monotonic)
    done = threading.Event()

    def wait() -> None:
        # Due 100 s from now at 1x; at 1000x it is already due.
        pace.wait_for(100.0, start_wall=start, clock=time.monotonic, sleep=None)
        done.set()

    worker = threading.Thread(target=wait, daemon=True)
    worker.start()
    time.sleep(0.2)
    assert not done.is_set()
    pace.set_speed(100_000.0)
    assert done.wait(timeout=2.0), "the wait did not wake on the speed change"


def test_a_replay_changed_mid_race_continues_at_the_new_rate_and_in_order() -> None:
    clock = _FakeClock()
    pace = ReplayPace(1.0)
    envelopes = replay_envelopes(
        HistoricalSource(HISTORICAL_ROOT),
        _key(SESSION_KEY),
        pace=pace,
        clock=clock.now,
        sleep=clock.sleep,
        wall_clock=lambda: FAKE_WALL_START,
    )
    stamped: list[tuple[float, float]] = []  # (wall offset, race time)
    for envelope in envelopes:
        if envelope.kind != "lap_completed":
            continue
        race_ts = datetime.fromisoformat(envelope.payload["ts_utc"]).timestamp()
        stamped.append(((envelope.event_time_utc - FAKE_WALL_START).total_seconds(), race_ts))
        if len(stamped) == 40:
            pace.set_speed(16.0)
        if len(stamped) == 120:
            break
    walls = [wall for wall, _ in stamped]
    assert walls == sorted(walls), "envelopes left out of order across the change"

    def rate(chunk: list[tuple[float, float]]) -> float:
        wall = chunk[-1][0] - chunk[0][0]
        race = chunk[-1][1] - chunk[0][1]
        return race / wall

    assert rate(stamped[5:39]) == pytest.approx(1.0, rel=0.05)
    assert rate(stamped[45:120]) == pytest.approx(16.0, rel=0.05)
    # No restart: race time keeps moving forward through the change.
    races = [race for _, race in stamped]
    assert races == sorted(races)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"type": "speed", "speed": 4}', 4.0),
        ('{"type": "speed", "speed": 0.5}', 0.5),
        ('{"type": "speed", "speed": 0}', 0.0),
        ('{"type": "speed", "speed": -1}', None),
        ('{"type": "speed", "speed": 1000}', None),
        ('{"type": "speed", "speed": true}', None),
        ('{"type": "speed", "speed": "4"}', None),
        ('{"type": "pause"}', None),
        ("not json", None),
        ("[4]", None),
    ],
)
def test_only_a_well_formed_speed_message_is_read(text: str, expected: float | None) -> None:
    assert replay_speed_from(text) == expected
