"""Measured car positions on the replay timeline: batched into one
`car_positions` envelope per instant, paced and remapped exactly like every
other envelope (so Delay Sync gates them identically), and never sent when
no viewer could see them. See `f1_api.stream.replay`'s module docstring."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from f1_api.config import Settings
from f1_api.geometry import session_positions
from f1_api.stream.replay import (
    CAR_POSITIONS_KIND,
    DEFAULT_STREAM_KINDS,
    POSITION_STALE_AFTER_S,
    LapSnapshotFeed,
    live_position_frames,
    replay_envelopes,
)
from f1_domain import CarPosition, DomainEvent, LapCompleted, Provenance, SessionKey, SessionType
from f1_kernel import MemorySource, PositionFrame, track_from_events
from f1_kernel.circuit import circuit_for_session

KEY = SessionKey(year=2026, round=14, session_type=SessionType.RACE)
LIGHTS_OUT = datetime(2026, 9, 13, 13, 3, tzinfo=UTC)
WALL = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _at(seconds: float) -> datetime:
    return LIGHTS_OUT + timedelta(seconds=seconds)


def _lap(driver: str, seconds: float, lap: int) -> LapCompleted:
    return LapCompleted(
        session_key=KEY,
        ts_utc=_at(seconds),
        source_seq=0,
        provenance=Provenance.LIVE,
        driver=driver,
        lap_number=lap,
    )


def _position(driver: str, seconds: float, x: float, y: float) -> CarPosition:
    return CarPosition(
        session_key=KEY,
        ts_utc=_at(seconds),
        source_seq=0,
        provenance=Provenance.LIVE,
        driver=driver,
        x=x,
        y=y,
        z=0.0,
        status="OnTrack",
    )


def _race() -> list[DomainEvent]:
    """Lap 1 ends at +0 s (the pacing anchor); positions every 0.25 s."""
    events: list[DomainEvent] = [_lap("1", 0.0, 1), _lap("1", 90.0, 2)]
    for step in range(4):
        t = step * 0.25
        events += [_position("1", t, 100 + step, 200), _position("44", t, 50 + step, 60.5)]
    return events


def _replay(source: MemorySource, **kwargs: object) -> list[tuple[str, float, dict[str, object]]]:
    fake = _FakeClock()
    envelopes = replay_envelopes(
        source,
        KEY,
        clock=fake.now,
        sleep=fake.sleep,
        wall_clock=lambda: WALL,
        **kwargs,  # type: ignore[arg-type]
    )
    return [
        (e.kind, (e.event_time_utc - WALL).total_seconds(), e.payload)
        for e in envelopes
        if e.kind in {CAR_POSITIONS_KIND, "car_position", "lap_completed"}
    ]


def test_positions_go_out_as_one_frame_per_instant() -> None:
    sent = _replay(MemorySource(_race()), speed=1.0)

    frames = [(offset, payload) for kind, offset, payload in sent if kind == CAR_POSITIONS_KIND]
    assert [offset for offset, _ in frames] == pytest.approx([0.0, 0.25, 0.5, 0.75])
    assert frames[0][1] == {"cars": {"1": [100, 200], "44": [50, 60.5]}}
    # Never one envelope per sample.
    assert all(kind != "car_position" for kind, _, _ in sent)


def test_frames_are_paced_on_the_same_timeline_as_events() -> None:
    sent = _replay(MemorySource(_race()), speed=4.0)

    offsets = [offset for _, offset, _ in sent]
    assert offsets == sorted(offsets)
    frame_offsets = [offset for kind, offset, _ in sent if kind == CAR_POSITIONS_KIND]
    assert frame_offsets == pytest.approx([0.0, 0.0625, 0.125, 0.1875])
    lap_two = next(o for kind, o, p in sent if kind == "lap_completed" and p["lap_number"] == 2)
    assert lap_two == pytest.approx(90.0 / 4.0)


@pytest.mark.parametrize("speed", [1.0, 16.0])
def test_the_pre_race_hour_is_cut_in_race_time_whatever_the_speed(speed: float) -> None:
    long_ago = -(POSITION_STALE_AFTER_S + 60.0)
    recent = -(POSITION_STALE_AFTER_S - 60.0)
    events = [
        *_race(),
        _position("1", long_ago, 1, 1),
        _position("1", recent, 2, 2),
    ]

    sent = _replay(MemorySource(events), speed=speed)

    first = next(payload for kind, _, payload in sent if kind == CAR_POSITIONS_KIND)
    assert first == {"cars": {"1": [2, 2]}}


def test_unpaced_replay_sends_only_the_final_frame() -> None:
    sent = _replay(MemorySource(_race()), speed=0)

    frames = [payload for kind, _, payload in sent if kind == CAR_POSITIONS_KIND]
    assert frames == [{"cars": {"1": [103, 200], "44": [53, 60.5]}}]


def test_positions_can_be_left_out() -> None:
    sent = _replay(MemorySource(_race()), speed=0, kinds=DEFAULT_STREAM_KINDS - {"car_position"})

    assert [kind for kind, _, _ in sent] == ["lap_completed", "lap_completed"]


def test_a_supplied_track_wins_over_the_source() -> None:
    laps_only = [e for e in _race() if isinstance(e, LapCompleted)]
    track = track_from_events(_race())

    sent = _replay(MemorySource(laps_only), speed=1.0, positions=track)

    assert sum(kind == CAR_POSITIONS_KIND for kind, _, _ in sent) == 4


def test_live_batching_folds_one_instant_into_one_frame() -> None:
    events = sorted(_race(), key=lambda e: e.ts_utc)

    items = list(live_position_frames(events))

    frames = [item for item in items if isinstance(item, PositionFrame)]
    assert [len(frame.cars) for frame in frames] == [2, 2, 2, 2]
    assert frames[0].cars == (("1", 100.0, 200.0), ("44", 50.0, 60.5))
    assert [item.kind for item in items if not isinstance(item, PositionFrame)] == [
        "lap_completed",
        "lap_completed",
    ]


def test_a_live_ingested_session_reads_the_post_session_archive(tmp_path: Path) -> None:
    """The store never receives positions live; `f1-backfill
    --positions-only --uncatalogued` files them under the historical root
    by session key, and the replay must find them there."""
    session_dir = tmp_path / "season=2026" / "round=14" / "session=Race"
    session_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "DriverNumber": ["4", "81"],
            "Date": pd.to_datetime(["2026-09-13 13:03:00", "2026-09-13 13:03:00"]),
            "X": [10, 20],
            "Y": [30, 40],
            "Z": [0, 0],
            "Status": ["OnTrack", "OnTrack"],
        }
    ).to_parquet(session_dir / "car_positions.parquet", index=False)
    store_like = MemorySource([e for e in _race() if isinstance(e, LapCompleted)])

    _, track = session_positions(Settings(historical_root=tmp_path), store_like, KEY, None)

    assert track is not None
    assert track["driver"].to_list() == ["4", "81"]


def test_a_session_with_no_positions_anywhere_has_none(tmp_path: Path) -> None:
    store_like = MemorySource([e for e in _race() if isinstance(e, LapCompleted)])

    assert session_positions(Settings(historical_root=tmp_path), store_like, KEY, None)[1] is None


def test_archive_positions_are_put_on_a_live_sessions_own_clock(tmp_path: Path) -> None:
    """The store stamps laps on receipt, seconds after the cars' own
    position timestamps; the replay must line the two up, or every car
    crosses the line before the timing tower counts its lap."""
    circuit = circuit_for_session("Bahrain Grand Prix")
    assert circuit is not None and circuit.outline is not None
    line_x, line_y = circuit.outline.points[0]
    rows: list[dict[str, object]] = []
    laps: list[DomainEvent] = []
    for lap in range(25):
        crossing = LIGHTS_OUT + timedelta(seconds=95 * lap)
        for step in range(-8, 9):
            rows.append(
                {
                    "DriverNumber": "1",
                    "Date": (crossing + timedelta(seconds=step * 0.25)).replace(tzinfo=None),
                    "X": int(line_x) + step * 100,
                    "Y": int(line_y),
                    "Z": 0,
                    "Status": "OnTrack",
                }
            )
        laps.append(_lap("1", 95 * lap + 2.0, lap + 1))
    session_dir = tmp_path / "season=2026" / "round=14" / "session=Race"
    session_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(session_dir / "car_positions.parquet", index=False)

    _, track = session_positions(
        Settings(historical_root=tmp_path), MemorySource(laps), KEY, circuit
    )

    assert track is not None
    first_crossing = track.filter(pl.col("x") == int(line_x))["ts_utc"][0]
    assert first_crossing == LIGHTS_OUT + timedelta(seconds=2.0)


class _CountingSession:
    """Stands in for `LiveKernelSession`: counts recomputes."""

    def __init__(self) -> None:
        self.pushes = 0

    def push(self, _events: list[DomainEvent]) -> None:
        self.pushes += 1


def test_a_lapped_car_finishing_an_older_lap_does_not_recompute_it() -> None:
    """Re-flushing on every lap number that merely differs from the last
    one made a 57-lap race recompute the kernel 260 times, each one a
    pause in the stream."""
    feed = LapSnapshotFeed(KEY)
    counting = _CountingSession()
    feed._live = counting  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]

    for event in [
        _lap("1", 0, 1),
        _lap("44", 1, 1),
        _lap("1", 90, 2),
        _lap("44", 95, 1),
        _lap("44", 100, 2),
        _lap("1", 180, 3),
    ]:
        feed.feed(event)

    assert counting.pushes == 3  # laps 1, 2, 3: nothing for 44's older laps


def test_leaving_mid_replay_stops_the_snapshot_worker() -> None:
    before = {t.ident for t in threading.enumerate() if t.name == "f1-api-snapshots"}
    fake = _FakeClock()
    envelopes = replay_envelopes(
        MemorySource(_race()),
        KEY,
        speed=1.0,
        clock=fake.now,
        sleep=fake.sleep,
        wall_clock=lambda: WALL,
    )
    next(envelopes)

    envelopes.close()

    for thread in threading.enumerate():
        if thread.name == "f1-api-snapshots" and thread.ident not in before:
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_samples_off_the_circuit_never_reach_the_stream(tmp_path: Path) -> None:
    """A placeholder coordinate every car reports at once (2025 Jeddah FP1,
    1.5 km from the track) must not be drawn, nor glided to."""
    circuit = circuit_for_session("Bahrain Grand Prix")
    assert circuit is not None and circuit.outline is not None
    on_x, on_y = circuit.outline.points[10]
    session_dir = tmp_path / "season=2026" / "round=14" / "session=Race"
    session_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "DriverNumber": ["4", "4", "81"],
            "Date": pd.to_datetime(
                ["2026-09-13 13:03:00", "2026-09-13 13:03:01", "2026-09-13 13:03:00"]
            ),
            "X": [int(on_x), -18631, int(on_x)],
            "Y": [int(on_y), -6898, int(on_y)],
            "Z": [0, 0, 0],
            "Status": ["OnTrack"] * 3,
        }
    ).to_parquet(session_dir / "car_positions.parquet", index=False)
    store_like = MemorySource([e for e in _race() if isinstance(e, LapCompleted)])

    _, track = session_positions(Settings(historical_root=tmp_path), store_like, KEY, circuit)

    assert track is not None
    assert track["x"].to_list() == [float(int(on_x))] * 2


def test_a_session_in_another_frame_draws_its_own_geometry(tmp_path: Path) -> None:
    """2025 Suzuka sits 13-30 m off its 2024 outline, beyond any rotation,
    scale or shift. When the circuit file does not fit a session's own
    positions, the racing line comes from that session's fastest laps."""
    circuit = circuit_for_session("Bahrain Grand Prix")
    assert circuit is not None and circuit.outline is not None
    loop = [(x, y + 400.0) for x, y in circuit.outline.points[:-1]]  # 40 m off
    lap_s = 90.0
    rows: list[dict[str, object]] = []
    laps: list[DomainEvent] = []
    for lap in range(6):
        start = LIGHTS_OUT + timedelta(seconds=lap * lap_s)
        for i, (x, y) in enumerate(loop):
            at = start + timedelta(seconds=lap_s * i / len(loop))
            rows.append(
                {
                    "DriverNumber": "1",
                    "Date": at.replace(tzinfo=None),
                    "X": int(x),
                    "Y": int(y),
                    "Z": 0,
                    "Status": "OnTrack",
                }
            )
        laps.append(
            LapCompleted(
                session_key=KEY,
                ts_utc=start,
                source_seq=0,
                provenance=Provenance.LIVE,
                driver="1",
                lap_number=lap,
                lap_time=timedelta(seconds=lap_s),
            )
        )
    session_dir = tmp_path / "season=2026" / "round=14" / "session=Race"
    session_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(session_dir / "car_positions.parquet", index=False)

    geometry, track = session_positions(
        Settings(historical_root=tmp_path), MemorySource(laps), KEY, circuit
    )

    assert geometry.from_session
    assert geometry.outline is not None
    mean_y = sum(y for _, y in geometry.outline.points) / len(geometry.outline.points)
    shipped_y = sum(y for _, y in circuit.outline.points) / len(circuit.outline.points)
    assert abs(mean_y - (shipped_y + 400.0)) < 30
    assert track is not None and len(track) == len(rows)


def test_a_session_that_fits_its_circuit_keeps_the_circuit_file(tmp_path: Path) -> None:
    circuit = circuit_for_session("Bahrain Grand Prix")
    assert circuit is not None and circuit.outline is not None
    session_dir = tmp_path / "season=2026" / "round=14" / "session=Race"
    session_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "DriverNumber": ["1"] * 40,
            "Date": pd.date_range("2026-09-13 13:03:00", periods=40, freq="250ms"),
            "X": [int(x) for x, _ in circuit.outline.points[:40]],
            "Y": [int(y) for _, y in circuit.outline.points[:40]],
            "Z": [0] * 40,
            "Status": ["OnTrack"] * 40,
        }
    ).to_parquet(session_dir / "car_positions.parquet", index=False)

    geometry, _ = session_positions(
        Settings(historical_root=tmp_path), MemorySource([]), KEY, circuit
    )

    assert not geometry.from_session
    assert geometry.outline == circuit.outline
    assert geometry.pit_lane == circuit.pit_lane
