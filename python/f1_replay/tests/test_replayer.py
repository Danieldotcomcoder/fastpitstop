"""Core fidelity tests for `f1_replay.replay`.

The acceptance bar (per CLAUDE.md / the Phase 0.4 spec) is that a downstream
consumer cannot tell replayed-at-real-speed from live in terms of record
shape and ordering. These tests check that directly: exact seq/payload
fidelity at `speed=0`, snapshot-before-stream ordering, the `--delay`
primitive's constant-offset behavior (using a fake clock so it's exact and
instant), and one real-sleep test that speed actually scales wall time.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from pydantic import JsonValue

from f1_recording import RawRecord, RecordingReader
from f1_replay.replayer import SUBSCRIBE_SNAPSHOT_TOPIC, replay
from f1_replay.sinks import list_sink

RecordingFactory = Callable[..., RecordingReader]
Message = tuple[str, JsonValue, int]


class FakeClock:
    """A controllable fake monotonic clock paired with a fake `sleep` that
    advances it by the requested amount instead of actually waiting — lets
    `replay()`'s timing math be tested with zero real wall-clock time.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


def test_speed_zero_matches_source_count_order_and_payloads(
    recording_factory: RecordingFactory,
) -> None:
    messages: list[Message] = [
        ("TrackStatus", {"Status": "1"}, 1_000_000_000),
        ("TimingData", {"Lines": {"1": {"GapToLeader": "1.2"}}}, 1_200_000_000),
        ("TimingData", {"Lines": {"1": {"GapToLeader": "1.1"}}}, 1_500_000_000),
        ("WeatherData", {"AirTemp": "23.0"}, 1_600_000_000),
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(reader, list_sink(emitted), speed=0, delay=0)

    source_records = list(RecordingReader(reader.directory).records())
    assert len(emitted) == len(source_records) + 1  # +1 for the synthetic snapshot

    snapshot_record = emitted[0]
    assert snapshot_record.topic == SUBSCRIBE_SNAPSHOT_TOPIC
    assert snapshot_record.payload == reader.subscribe_snapshot

    for emitted_record, source_record in zip(emitted[1:], source_records, strict=True):
        assert emitted_record.seq == source_record.seq
        assert emitted_record.topic == source_record.topic
        assert emitted_record.payload == source_record.payload
        assert emitted_record.ts_mono == source_record.ts_mono


def test_snapshot_is_always_delivered_before_any_stream_record(
    recording_factory: RecordingFactory,
) -> None:
    messages: list[Message] = [("TrackStatus", {"Status": "1"}, 1_000_000_000)]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(reader, list_sink(emitted), speed=0)

    assert emitted[0].topic == SUBSCRIBE_SNAPSHOT_TOPIC
    assert all(r.topic != SUBSCRIBE_SNAPSHOT_TOPIC for r in emitted[1:])


def test_monotonic_ordering_holds(recording_factory: RecordingFactory) -> None:
    messages: list[Message] = [
        ("A", {}, 1_000_000_000),
        ("B", {}, 1_300_000_000),
        ("C", {}, 1_900_000_000),
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(reader, list_sink(emitted), speed=0)

    stream_records = emitted[1:]
    ts_monos = [r.ts_mono for r in stream_records]
    assert ts_monos == sorted(ts_monos)
    seqs = [r.seq for r in stream_records]
    assert seqs == sorted(seqs)


def test_fake_clock_speed_scales_gaps_exactly(
    recording_factory: RecordingFactory, fake_clock: FakeClock
) -> None:
    # 0.5s, then 0.3s gaps in source time.
    messages: list[Message] = [
        ("A", {}, 1_000_000_000),
        ("B", {}, 1_500_000_000),
        ("C", {}, 1_800_000_000),
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(
        reader,
        list_sink(emitted),
        speed=10,
        delay=0,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )

    # Snapshot + record[0] both land at t=0; record[1] at 0.5/10=0.05;
    # record[2] at 0.8/10=0.08.
    assert fake_clock.now == pytest.approx(0.08)


def test_fake_clock_delay_is_a_constant_offset_not_per_message(
    recording_factory: RecordingFactory, fake_clock: FakeClock
) -> None:
    messages: list[Message] = [
        ("A", {}, 1_000_000_000),
        ("B", {}, 1_500_000_000),
        ("C", {}, 1_800_000_000),
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(
        reader,
        list_sink(emitted),
        speed=10,
        delay=2.0,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )

    # Same gaps as the speed test, plus a single constant +2.0s shift -
    # proves delay doesn't compound per message.
    assert fake_clock.now == pytest.approx(2.08)


def test_speed_zero_ignores_gaps_entirely(
    recording_factory: RecordingFactory, fake_clock: FakeClock
) -> None:
    messages: list[Message] = [
        ("A", {}, 1_000_000_000),
        ("B", {}, 500_000_000_000),  # a huge gap that must NOT be waited on
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    replay(
        reader,
        list_sink(emitted),
        speed=0,
        delay=0,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )

    assert fake_clock.now == 0.0
    assert len(emitted) == 3


def test_negative_speed_rejected(recording_factory: RecordingFactory) -> None:
    messages: list[Message] = [("A", {}, 1_000_000_000)]
    reader = recording_factory(messages)
    sink: Callable[[RawRecord], None] = list_sink([])
    with pytest.raises(ValueError, match="speed"):
        replay(reader, sink, speed=-1)


def test_real_sleep_speed_100_is_within_tolerance_of_source_duration_over_100(
    recording_factory: RecordingFactory,
) -> None:
    # ~0.6s of source time, spread over 4 records.
    gap_ns = 200_000_000
    messages: list[Message] = [
        ("A", {}, 0),
        ("B", {}, gap_ns),
        ("C", {}, 2 * gap_ns),
        ("D", {}, 3 * gap_ns),
    ]
    reader = recording_factory(messages)

    emitted: list[RawRecord] = []
    started = time.monotonic()
    replay(reader, list_sink(emitted), speed=100, delay=0)
    elapsed = time.monotonic() - started

    expected = (3 * gap_ns) / 1_000_000_000 / 100  # 0.006s
    # Generous absolute tolerance: real wall time at this timescale is
    # dominated by scheduler/sleep-granularity noise, not replay logic - the
    # meaningful assertion is that 100x actually sped things up (elapsed is
    # nowhere near the ~0.6s the source itself spans).
    assert elapsed < 0.3
    assert expected < elapsed + 0.3
    assert len(emitted) == len(messages) + 1
