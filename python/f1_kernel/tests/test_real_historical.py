"""Opt-in tests against REAL backfilled races.

The rest of the suite runs on a checked-in synthetic fixture so CI stays
green on a machine with no data. These tests run the same code over actual
`f1-backfill` output when it exists locally, and skip otherwise. They are
the ones that catch what synthetic data cannot: FastF1's real null
patterns, OpenF1's real race-control vocabulary, drivers retiring, laps the
feed simply missed.

Run them by backfilling first (see docs/PHASE0.md):

    uv run f1-backfill --season 2024

They assert RANGES and KNOWN FACTS about specific 2024 races, not exact
values — the point is to catch a filter that has silently stopped working,
not to pin numbers that legitimately depend on the data.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from kernel_fixture import number

from f1_domain import SessionKey, SessionType
from f1_kernel import (
    Backtest,
    HistoricalSource,
    KernelParams,
    SessionFrames,
    annotate_laps,
    clean_lap_summary,
    lap_frame,
    list_historical_sessions,
    stint_frame,
)

REAL_ROOT = Path(__file__).parents[3] / "data" / "historical"

# Known facts about specific 2024 races, from the race-control record.
BAHRAIN = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
JEDDAH = SessionKey(year=2024, round=2, session_type=SessionType.RACE)  # safety car, laps 7-9
AUSTRALIA = SessionKey(year=2024, round=3, session_type=SessionType.RACE)  # VSC, lap 17


def _available(key: SessionKey) -> bool:
    return any(ref.session_key == key for ref in list_historical_sessions(REAL_ROOT))


requires_backfill = pytest.mark.skipif(
    not REAL_ROOT.is_dir() or not list_historical_sessions(REAL_ROOT),
    reason="no local backfill; run `uv run f1-backfill --season 2024`",
)


@pytest.fixture(scope="module")
def real_source() -> HistoricalSource:
    return HistoricalSource(REAL_ROOT)


@requires_backfill
def test_real_lap_frame_is_sane(real_source: HistoricalSource) -> None:
    if not _available(BAHRAIN):
        pytest.skip("2024 Bahrain race not backfilled")
    laps = lap_frame(real_source, BAHRAIN)

    assert laps.height > 900  # 20 cars x ~57 laps
    assert laps["driver"].n_unique() == 20
    # Real races contain a couple of missing lap times, always pit-adjacent
    # or on a lap the car did not finish — never on a clean racing lap.
    racing = laps.filter(~pl.col("is_pit_in") & ~pl.col("is_pit_out"))
    assert racing["lap_time_s"].null_count() / racing.height < 0.01
    assert number(laps["lap_time_s"].min()) > 60
    assert laps["compound"].null_count() == 0


@requires_backfill
def test_real_tyre_age_is_monotonic_within_stints(real_source: HistoricalSource) -> None:
    if not _available(BAHRAIN):
        pytest.skip("2024 Bahrain race not backfilled")
    laps = lap_frame(real_source, BAHRAIN)

    for (driver, stint), group in laps.group_by(["driver", "stint_number"]):
        ages = group.sort("lap_number")["tyre_age"].to_list()
        assert ages == sorted(ages), f"{driver} stint {stint}: {ages}"


@requires_backfill
def test_real_stints_cover_every_lap(real_source: HistoricalSource) -> None:
    if not _available(BAHRAIN):
        pytest.skip("2024 Bahrain race not backfilled")
    laps = lap_frame(real_source, BAHRAIN)
    stints = stint_frame(real_source, BAHRAIN)

    laps_per_driver = laps.group_by("driver").agg(pl.len().alias("laps"))
    stint_laps = stints.group_by("driver").agg(pl.col("lap_count").sum().alias("laps"))
    joined = laps_per_driver.join(stint_laps, on="driver", suffix="_stints")

    assert joined.height == 20
    assert (joined["laps"] == joined["laps_stints"]).all()


@requires_backfill
def test_a_real_safety_car_race_flags_safety_car_laps(real_source: HistoricalSource) -> None:
    """2024 Saudi Arabia: safety car deployed on lap 7, in on lap 9. Those
    laps must be flagged, and a race with a safety car must lose a
    meaningful number of laps to it."""
    if not _available(JEDDAH):
        pytest.skip("2024 Saudi Arabian race not backfilled")
    annotated = annotate_laps(lap_frame(real_source, JEDDAH), KernelParams())

    sc_laps = annotated.filter(pl.col("is_sc"))
    assert not sc_laps.is_empty()
    # Every car is neutralized together, so most of the field is affected.
    assert sc_laps["driver"].n_unique() >= 15
    assert set(sc_laps["lap_number"].unique().to_list()) & {7, 8, 9}
    # And the safety car is over well before the end of the race.
    assert number(sc_laps["lap_number"].max()) < 20


@requires_backfill
def test_a_real_vsc_race_flags_vsc_laps(real_source: HistoricalSource) -> None:
    """2024 Australia: a virtual safety car around lap 17-18, and no full
    safety car at all."""
    if not _available(AUSTRALIA):
        pytest.skip("2024 Australian race not backfilled")
    annotated = annotate_laps(lap_frame(real_source, AUSTRALIA), KernelParams())

    vsc = annotated.filter(pl.col("is_vsc"))
    assert not vsc.is_empty()
    assert set(vsc["lap_number"].unique().to_list()) & {17, 18}
    assert not annotated["is_sc"].any()


@requires_backfill
def test_real_pit_stops_are_flagged(real_source: HistoricalSource) -> None:
    if not _available(BAHRAIN):
        pytest.skip("2024 Bahrain race not backfilled")
    laps = lap_frame(real_source, BAHRAIN)
    stints = stint_frame(real_source, BAHRAIN)

    # One in-lap per stop, and a stop is what starts each stint after the
    # first, so the counts must agree per driver.
    stops = laps.filter(pl.col("is_pit_in")).group_by("driver").agg(pl.len().alias("stops"))
    extra_stints = (
        stints.group_by("driver").agg((pl.len() - 1).alias("stops")).filter(pl.col("stops") > 0)
    )
    joined = stops.join(extra_stints, on="driver", suffix="_stints")
    assert joined.height >= 15
    assert (joined["stops"] == joined["stops_stints"]).all()


@requires_backfill
def test_real_clean_lap_fraction_is_sensible(real_source: HistoricalSource) -> None:
    """The headline sanity check: on a real race, the filter must leave a
    usable fraction of laps. Too low and nothing can be fitted; too high and
    it is not filtering anything."""
    if not _available(BAHRAIN):
        pytest.skip("2024 Bahrain race not backfilled")
    summary = clean_lap_summary(lap_frame(real_source, BAHRAIN), KernelParams())

    fractions = summary["clean_fraction"]
    assert number(fractions.median()) > 0.25
    assert number(fractions.median()) < 0.95
    assert number(summary["clean_laps"].min()) >= 1


@requires_backfill
def test_backtest_runs_over_every_real_race_of_a_season(real_source: HistoricalSource) -> None:
    """The end-to-end proof that Phase 2 has a corpus: load every 2024 race
    and run an analytic over all of them without a single failure."""
    backtest = Backtest(real_source)
    races = backtest.sessions(session_types=[SessionType.RACE], seasons=[2024])
    if len(races) < 3:
        pytest.skip("fewer than 3 backfilled 2024 races")

    def clean_lap_share(frames: SessionFrames, params: KernelParams) -> dict[str, int]:
        summary = clean_lap_summary(frames.laps, params)
        return {
            "drivers": summary.height,
            "laps": int(summary["laps"].sum()),
            "clean_laps": int(summary["clean_laps"].sum()),
        }

    result = backtest.run(clean_lap_share, races)

    result.raise_for_errors()
    assert result.sessions_run == len(races)
    assert result.table.height == len(races)
    assert (result.table["clean_laps"] > 0).all()
    assert (result.table["clean_laps"] < result.table["laps"]).all()
