"""The kernel over LIVE-derived events, not just historical ones.

CLAUDE.md's Phase 2 invariant is that every analytic runs identically on
live-derived and historical-derived data. Historical is covered everywhere
else in this suite; this module drives the OTHER path — a real recording,
replayed through the real Phase 1 pipeline (`f1_pipeline`), into the kernel.

That path differs in ways the frame builders have to absorb:

- events carry `provenance=LIVE` and real wire `source_seq`s;
- track status comes from the feed's own `TrackStatusChanged` topic rather
  than being reconstructed from race control;
- gaps arrive as the timing feed's constant partial updates, and lapped
  cars send a lap count instead of a duration.

If this passes and the historical tests pass, the kernel is genuinely
source-agnostic.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1_domain import Provenance, SessionKey
from f1_kernel import (
    LAP_FRAME_SCHEMA,
    HistoricalSource,
    KernelParams,
    MemorySource,
    annotate_laps,
    clean_laps,
    lap_frame,
    stint_frame,
)

pytest.importorskip("f1_pipeline", reason="f1_pipeline is a dev-only dependency of the kernel")


@pytest.fixture(scope="module")
def live_source() -> MemorySource:
    """Replay the checked-in Phase 1 recording through the real pipeline and
    hand the kernel exactly what the live socket would have produced."""
    from f1_pipeline.pipeline import run
    from f1_pipeline.replay_source import replay_records
    from f1_pipeline.verify import RECORDING_DIR, SESSION_KEY
    from f1_store import MemorySink

    sink = MemorySink()
    run(replay_records(RECORDING_DIR, speed=0), sink, session_key=SESSION_KEY)
    return MemorySource(sink.events, event_names={SESSION_KEY: "Simulated Grand Prix"})


@pytest.fixture(scope="module")
def live_key() -> SessionKey:
    from f1_pipeline.verify import SESSION_KEY

    return SESSION_KEY


def test_lap_frame_builds_from_live_events(live_source: MemorySource, live_key: SessionKey) -> None:
    laps = lap_frame(live_source, live_key)

    assert dict(laps.schema) == LAP_FRAME_SCHEMA
    assert laps.height > 100
    assert set(laps["provenance"].to_list()) == {Provenance.LIVE.value}


def test_live_laps_agree_with_the_recordings_ground_truth(
    live_source: MemorySource, live_key: SessionKey
) -> None:
    """The Phase 1 fixture ships the race's independent ground truth; the
    kernel's frame must reproduce it."""
    from f1_pipeline.verify import load_expected

    expected = load_expected()
    laps = lap_frame(live_source, live_key)

    counts = {
        str(row["driver"]): int(row["laps"])
        for row in laps.group_by("driver").agg(pl.len().alias("laps")).to_dicts()
    }
    assert counts == {str(k): int(v) for k, v in expected["laps_by_driver"].items()}  # type: ignore[union-attr]

    pit_laps = {
        str(driver): sorted(
            int(lap)
            for lap in laps.filter(pl.col("is_pit_in") & (pl.col("driver") == driver))["lap_number"]
        )
        for driver in laps["driver"].unique()
    }
    expected_pits = {str(k): [int(x) for x in v] for k, v in expected["pit_laps"].items()}  # type: ignore[union-attr]
    assert {d: laps_ for d, laps_ in pit_laps.items() if laps_} == expected_pits


def test_live_track_status_comes_from_the_feeds_own_topic(
    live_source: MemorySource, live_key: SessionKey
) -> None:
    """On the live path the status timeline is built from
    `TrackStatusChanged`, not reconstructed from race control."""
    from f1_kernel import track_status_timeline

    timeline = track_status_timeline(live_source.events(live_key))

    assert timeline, "the recording contains track status changes"
    assert {interval.source for interval in timeline} == {"track_status"}


def test_stints_build_from_live_events(live_source: MemorySource, live_key: SessionKey) -> None:
    stints = stint_frame(live_source, live_key)

    assert stints.height > 0
    assert stints["compound"].null_count() == 0
    assert (stints["lap_count"] > 0).all()


def test_the_clean_lap_filter_runs_on_live_data(
    live_source: MemorySource, live_key: SessionKey
) -> None:
    """The same filter, the same columns, on a source that reconstructs
    nothing — and it must still leave a usable majority of laps."""
    laps = lap_frame(live_source, live_key)
    params = KernelParams()

    annotated = annotate_laps(laps, params)
    clean = clean_laps(laps, params)

    assert annotated.height == laps.height
    assert 0.4 < clean.height / laps.height < 1.0
    assert annotated.filter(pl.col("lap_number") == 1)["is_lap1"].all()
    assert annotated.filter(pl.col("is_pit_in"))["is_in_lap"].all()


def test_live_and_historical_frames_share_one_schema(
    live_source: MemorySource, live_key: SessionKey, source: HistoricalSource
) -> None:
    """The whole point: an analytic cannot tell the two apart by shape."""
    from kernel_fixture import SC_RACE

    live = lap_frame(live_source, live_key)
    historical = lap_frame(source, SC_RACE)

    assert live.columns == historical.columns
    assert dict(live.schema) == dict(historical.schema)


def test_the_undercut_snapshot_runs_on_live_frames(
    live_source: MemorySource, live_key: SessionKey
) -> None:
    """The Phase 3 per-lap primitive over genuinely live-derived frames:
    slice, refit, pair, project — no historical-only assumption anywhere."""
    from f1_kernel import WindowState, session_frames, undercut_snapshot

    frames = session_frames(live_source, live_key)
    results = undercut_snapshot(frames, KernelParams())

    assert results, "a full race snapshot with a packed field produced no pairs"
    for result in results:
        assert result.attacker != result.defender
        assert result.gap_s is not None and result.gap_s >= 0
        assert result.window in WindowState
        assert result.reasons is not None
