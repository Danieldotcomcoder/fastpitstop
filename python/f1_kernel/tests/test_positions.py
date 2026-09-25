"""Position tracks: the columnar twin of the `car_position` event stream.

The track must hold exactly what the events hold (same samples, same
instants), whichever source built it, and `position_frames` must regroup
it into what the feed sent: every car at one instant, per frame.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl
from kernel_fixture import HISTORICAL_ROOT, SC_RACE

from f1_domain import CarPosition, LapCompleted, Provenance, SessionKey
from f1_kernel import (
    HistoricalSource,
    MemorySource,
    near_lines,
    position_frames,
    session_clock_offset,
    shift_track,
    track_from_events,
)
from f1_kernel.positions import MIN_CLOCK_MATCHES, normalize_track

T0 = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)


def _position(driver: str, seconds: float, x: float, y: float) -> CarPosition:
    return CarPosition(
        session_key=SC_RACE,
        ts_utc=T0 + timedelta(seconds=seconds),
        source_seq=0,
        provenance=Provenance.HISTORICAL,
        driver=driver,
        x=x,
        y=y,
        z=0.0,
        status="OnTrack",
    )


def _events() -> list[CarPosition | LapCompleted]:
    return [
        _position("44", 0.0, 10, 20),
        _position("1", 0.0, 11, 21),
        LapCompleted(
            session_key=SC_RACE,
            ts_utc=T0 + timedelta(seconds=0.1),
            source_seq=0,
            provenance=Provenance.HISTORICAL,
            driver="1",
            lap_number=1,
        ),
        _position("1", 0.25, 15, 25),
        _position("44", 0.25, 14, 24),
    ]


def test_frames_regroup_samples_by_instant_with_every_car() -> None:
    track = track_from_events(_events())
    assert track is not None

    frames = list(position_frames(track))

    assert [frame.ts_utc for frame in frames] == [T0, T0 + timedelta(seconds=0.25)]
    assert frames[0].cars == (("1", 11.0, 21.0), ("44", 10.0, 20.0))
    assert frames[1].cars == (("1", 15.0, 25.0), ("44", 14.0, 24.0))


def test_a_stream_without_positions_has_no_track() -> None:
    assert track_from_events([e for e in _events() if isinstance(e, LapCompleted)]) is None


def test_a_naive_timestamp_is_read_as_utc() -> None:
    frame = pl.DataFrame(
        {"ts_utc": [datetime(2024, 3, 2, 15, 0)], "driver": ["1"], "x": [1], "y": [2]}
    )

    track = normalize_track(frame)

    assert track is not None
    assert track["ts_utc"].to_list() == [T0]


def test_memory_source_track_matches_its_events() -> None:
    source = MemorySource(_events())

    track = source.position_track(SC_RACE)
    other = source.position_track(SessionKey(year=2023, round=1, session_type=SC_RACE.session_type))

    assert track is not None and len(track) == 4
    assert other is None


def _with_positions(tmp_path: Path) -> Path:
    """The kernel fixture plus a `car_positions` table for its SC race."""
    root = tmp_path / "historical"
    shutil.copytree(HISTORICAL_ROOT, root)
    session = root / "season=2024" / "round=01" / "session=Race"
    pd.DataFrame(
        {
            "DriverNumber": ["1", "44", "1", "44"],
            "Date": pd.to_datetime(
                [
                    "2024-03-02 15:00:00.000",
                    "2024-03-02 15:00:00.000",
                    "2024-03-02 15:00:00.250",
                    "2024-03-02 15:00:00.250",
                ]
            ),
            "X": [11, 10, 15, 14],
            "Y": [21, 20, 25, 24],
            "Z": [0, 0, 0, 0],
            "Status": ["OnTrack"] * 4,
        }
    ).to_parquet(session / "car_positions.parquet", index=False)
    return root


def test_historical_track_is_the_car_positions_table(tmp_path: Path) -> None:
    source = HistoricalSource(_with_positions(tmp_path))

    track = source.position_track(SC_RACE)

    assert track is not None
    assert track.schema == pl.Schema(
        {
            "ts_utc": pl.Datetime("us", "UTC"),
            "driver": pl.String,
            "x": pl.Float64,
            "y": pl.Float64,
        }
    )
    assert [frame.cars for frame in position_frames(track)] == [
        (("1", 11.0, 21.0), ("44", 10.0, 20.0)),
        (("1", 15.0, 25.0), ("44", 14.0, 24.0)),
    ]


def test_historical_track_and_events_agree(tmp_path: Path) -> None:
    source = HistoricalSource(_with_positions(tmp_path))

    from_events = track_from_events(source.events(SC_RACE, kinds=["car_position"]))
    columnar = source.position_track(SC_RACE)

    assert from_events is not None and columnar is not None
    assert from_events.equals(columnar)


def test_position_events_are_part_of_the_unfiltered_stream(tmp_path: Path) -> None:
    """The pushdown contract: `kinds=["car_position"]` equals the full
    stream filtered to that kind."""
    source = HistoricalSource(_with_positions(tmp_path))

    everything = [e for e in source.events(SC_RACE) if e.kind == "car_position"]
    filtered = list(source.events(SC_RACE, kinds=["car_position"]))

    assert len(filtered) == len(everything) == 4


def test_a_session_without_the_table_has_no_track() -> None:
    assert HistoricalSource(HISTORICAL_ROOT).position_track(SC_RACE) is None


def test_the_archive_serves_a_session_the_catalog_does_not_list(tmp_path: Path) -> None:
    """How a live-ingested session gets positions: filed by key alone."""
    root = tmp_path / "historical"
    session = root / "season=2026" / "round=14" / "session=Race"
    session.mkdir(parents=True)
    pd.DataFrame(
        {
            "DriverNumber": ["4"],
            "Date": pd.to_datetime(["2026-09-13 13:05:00"]),
            "X": [1],
            "Y": [2],
            "Z": [0],
            "Status": ["OnTrack"],
        }
    ).to_parquet(session / "car_positions.parquet", index=False)
    key = SessionKey(year=2026, round=14, session_type=SC_RACE.session_type)

    source = HistoricalSource(root)

    assert source.sessions() == []
    track = source.position_track(key)
    assert track is not None and track["driver"].to_list() == ["4"]


def _laps_through_line(
    offset_s: float, laps: int = 25
) -> tuple[pl.DataFrame, list[tuple[str, datetime]]]:
    """One car crossing the line point (0, 0) every 90 s, sampled every
    0.25 s either side, and its laps logged `offset_s` after each crossing."""
    rows: list[tuple[datetime, str, float, float]] = []
    logged: list[tuple[str, datetime]] = []
    for lap in range(laps):
        crossing = T0 + timedelta(seconds=90 * lap)
        for step in range(-40, 41):
            rows.append((crossing + timedelta(seconds=step * 0.25), "1", 500.0 * step * 0.25, 0.0))
        logged.append(("1", crossing + timedelta(seconds=offset_s)))
    track = normalize_track(pl.DataFrame(rows, schema=["ts_utc", "driver", "x", "y"], orient="row"))
    assert track is not None
    return track, logged


def test_clock_offset_is_how_late_the_session_logged_each_crossing() -> None:
    track, logged = _laps_through_line(2.4)

    offset = session_clock_offset(track, logged, (0.0, 0.0))

    assert offset == timedelta(seconds=2.4)
    shifted = shift_track(track, timedelta(seconds=2.4))
    assert shifted["ts_utc"][0] == track["ts_utc"][0] + timedelta(seconds=2.4)


def test_too_few_matched_laps_is_no_offset() -> None:
    track, logged = _laps_through_line(2.4, laps=MIN_CLOCK_MATCHES - 1)

    assert session_clock_offset(track, logged, (0.0, 0.0)) is None


def test_a_lap_whose_car_was_never_near_the_line_is_ignored() -> None:
    track, logged = _laps_through_line(2.4)

    # The line point is nowhere near the car: nothing matches.
    assert session_clock_offset(track, logged, (0.0, 50_000.0)) is None
    # A lap for a car with no samples at all is skipped, not fatal.
    assert session_clock_offset(track, [*logged, ("99", T0)], (0.0, 0.0)) == timedelta(seconds=2.4)


def test_samples_nowhere_near_the_circuit_are_dropped() -> None:
    """2025 Jeddah FP1: every car reported one coordinate 1.5 km from the
    track for twenty minutes."""
    lap = [(0.0, 0.0), (20000.0, 0.0), (20000.0, 16000.0), (0.0, 16000.0), (0.0, 0.0)]
    pit = [(1000.0, -150.0), (5000.0, -150.0)]
    rows = [
        (T0, "1", 10000.0, 40.0),  # on the racing line
        (T0, "44", 3000.0, -160.0),  # in the pit lane
        (T0, "63", -18631.0, -6898.0),  # the placeholder
        (T0, "81", 10000.0, 8000.0),  # mid-infield, 800 m from any line
    ]
    track = normalize_track(pl.DataFrame(rows, schema=["ts_utc", "driver", "x", "y"], orient="row"))
    assert track is not None

    kept = near_lines(track, [lap, pit])

    assert sorted(kept["driver"].to_list()) == ["1", "44"]
