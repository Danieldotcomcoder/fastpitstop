"""The lap/stint/telemetry frames: shape, schema, and the invariants every
later analytic silently relies on."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import polars as pl
import pytest
from kernel_fixture import GREEN_RACE, SC_RACE, number, truth_key

from f1_domain import Provenance, SessionKey, SessionType
from f1_kernel import (
    LAP_FRAME_DOCS,
    LAP_FRAME_SCHEMA,
    STINT_FRAME_SCHEMA,
    TELEMETRY_FRAME_SCHEMA,
    HistoricalSource,
    MemorySource,
    lap_frame,
    lap_frame_from_events,
    session_frames,
    stint_frame,
    telemetry_frame,
)

# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_lap_frame_matches_the_declared_schema(source: HistoricalSource) -> None:
    laps = lap_frame(source, SC_RACE)

    assert laps.columns == list(LAP_FRAME_SCHEMA)
    assert dict(laps.schema) == LAP_FRAME_SCHEMA


def test_every_lap_frame_column_is_documented() -> None:
    """A column nobody documented is a column nobody can use correctly.
    Identity columns are self-explanatory; everything else needs a note."""
    self_evident = {"session_year", "session_round", "session_type"}
    undocumented = set(LAP_FRAME_SCHEMA) - set(LAP_FRAME_DOCS) - self_evident

    assert undocumented == set()


def test_lap_frame_has_expected_shape(source: HistoricalSource, truth: dict[str, Any]) -> None:
    laps = lap_frame(source, SC_RACE)
    expected = truth[truth_key(SC_RACE)]

    assert laps.height == sum(expected["laps_by_driver"].values())
    assert sorted(laps["driver"].unique()) == sorted(expected["drivers"])
    counts = {
        str(row["driver"]): int(row["laps"])
        for row in laps.group_by("driver").agg(pl.len().alias("laps")).to_dicts()
    }
    assert counts == {driver: int(n) for driver, n in expected["laps_by_driver"].items()}


# ---------------------------------------------------------------------------
# the invariants
# ---------------------------------------------------------------------------


def test_completed_laps_have_lap_times(source: HistoricalSource, truth: dict[str, Any]) -> None:
    """A lap with no time is only ever a pit-adjacent one.

    The feed genuinely drops `LapTime` on some in/out laps (real 2024
    backfills contain a couple per race), so this asserts the shape of the
    exception rather than that it never happens: every null lap time
    belongs to a lap the driver entered or left the pits on.
    """
    laps = lap_frame(source, SC_RACE)
    missing = laps.filter(pl.col("lap_time_s").is_null())

    expected_driver, expected_lap = truth[truth_key(SC_RACE)]["missing_lap_time"]
    assert missing.height == 1
    assert missing["driver"][0] == expected_driver
    assert missing["lap_number"][0] == expected_lap
    assert missing["is_pit_in"][0] or missing["is_pit_out"][0]

    racing = laps.filter(~pl.col("is_pit_in") & ~pl.col("is_pit_out"))
    assert racing["lap_time_s"].null_count() == 0


def test_tyre_age_is_monotonic_within_a_stint(source: HistoricalSource) -> None:
    """Tyre age must only ever tick up inside a stint, and reset on a new
    one. A hole or a reset mid-stint means degradation fitting is being fed
    a lie."""
    laps = lap_frame(source, SC_RACE).sort("driver", "lap_number")

    for (driver, stint), group in laps.group_by(["driver", "stint_number"], maintain_order=True):
        ages = group.sort("lap_number")["tyre_age"].to_list()
        assert all(a is not None for a in ages), f"{driver} stint {stint} has a null tyre age"
        assert ages == sorted(ages), f"{driver} stint {stint} tyre age not monotonic: {ages}"
        # One lap on track = one lap of tyre life, so it steps by exactly 1.
        assert all(b - a == 1 for a, b in pairwise(ages))


def test_tyre_age_resets_on_a_new_stint(source: HistoricalSource) -> None:
    laps = lap_frame(source, SC_RACE)
    second_stints = laps.filter(pl.col("stint_number") == 2).sort("driver", "lap_number")

    first_laps = second_stints.group_by("driver").agg(pl.col("tyre_age").min())
    assert set(first_laps["tyre_age"].to_list()) == {1}


def test_pit_laps_match_ground_truth(source: HistoricalSource, truth: dict[str, Any]) -> None:
    laps = lap_frame(source, SC_RACE)
    expected = truth[truth_key(SC_RACE)]

    actual_in = _laps_where(laps, "is_pit_in")
    actual_out = _laps_where(laps, "is_pit_out")
    assert actual_in == {d: list(v) for d, v in expected["pit_in_laps"].items()}
    assert actual_out == {d: list(v) for d, v in expected["pit_out_laps"].items()}


def test_dirty_air_stint_shows_up_as_a_small_gap(
    source: HistoricalSource, truth: dict[str, Any]
) -> None:
    """The gap channel is summarized per lap, so the driver planted in
    another's wake must read as a sub-second gap on exactly those laps."""
    laps = lap_frame(source, SC_RACE)
    driver, expected_laps = next(iter(truth[truth_key(SC_RACE)]["dirty_air_laps"].items()))

    close = laps.filter((pl.col("driver") == driver) & (pl.col("gap_ahead_s") < 1.0))
    assert close["lap_number"].to_list() == expected_laps
    assert number(close["gap_ahead_samples"].min()) > 3


def test_leader_has_no_gap_ahead(source: HistoricalSource) -> None:
    """No car ahead means the channel is NULL, never zero — a zero gap would
    look like the tightest possible dirty air."""
    laps = lap_frame(source, SC_RACE).filter(pl.col("driver") == "1")

    assert laps["gap_ahead_s"].null_count() == laps.height
    assert set(laps["gap_ahead_samples"].to_list()) == {0}


def test_frames_are_deterministic(source: HistoricalSource) -> None:
    """Same source, same session, byte-identical frames. Without this,
    nothing built on top can be backtested."""
    first = lap_frame(source, SC_RACE)
    second = lap_frame(source, SC_RACE)

    assert first.equals(second)
    assert first.write_ipc(None).getvalue() == second.write_ipc(None).getvalue()


def test_historical_frames_are_marked_historical(source: HistoricalSource) -> None:
    laps = lap_frame(source, SC_RACE)

    assert set(laps["provenance"].to_list()) == {Provenance.HISTORICAL.value}


# ---------------------------------------------------------------------------
# stints
# ---------------------------------------------------------------------------


def test_stint_frame_matches_schema_and_truth(
    source: HistoricalSource, truth: dict[str, Any]
) -> None:
    stints = stint_frame(source, SC_RACE)
    expected = truth[truth_key(SC_RACE)]

    assert dict(stints.schema) == STINT_FRAME_SCHEMA
    assert stints.height == len(expected["drivers"]) * 2

    compounds = {
        str(driver): group.sort("stint_number")["compound"].to_list()
        for (driver, *_), group in stints.group_by(["driver"], maintain_order=True)
    }
    assert compounds == {
        driver: [c.lower() for c in seq] for driver, seq in expected["stint_compounds"].items()
    }


def test_stint_laps_tile_the_race(source: HistoricalSource, truth: dict[str, Any]) -> None:
    """Stints must cover every lap exactly once — a gap or an overlap means
    a lap would be dropped from, or double-counted in, a degradation fit."""
    stints = stint_frame(source, SC_RACE)
    total_laps = truth[truth_key(SC_RACE)]["total_laps"]

    for (driver,), group in stints.group_by(["driver"], maintain_order=True):
        ordered = group.sort("stint_number")
        starts = ordered["start_lap"].to_list()
        ends = ordered["end_lap"].to_list()
        assert starts[0] == 1, driver
        assert ends[-1] == total_laps, driver
        assert all(nxt == prev + 1 for prev, nxt in zip(ends, starts[1:], strict=False))
        assert ordered["lap_count"].sum() == total_laps


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------


def test_telemetry_frame_matches_schema(source: HistoricalSource, truth: dict[str, Any]) -> None:
    driver = truth[truth_key(SC_RACE)]["telemetry_driver"]
    telemetry = telemetry_frame(source, SC_RACE, driver)

    assert dict(telemetry.schema) == TELEMETRY_FRAME_SCHEMA
    assert telemetry.height > 0
    assert set(telemetry["driver"].to_list()) == {driver}


def test_telemetry_distance_only_ever_increases(
    source: HistoricalSource, truth: dict[str, Any]
) -> None:
    """Distance is integrated from speed, so it must be non-decreasing —
    a dip would mean negative speed or an out-of-order sample."""
    driver = truth[truth_key(SC_RACE)]["telemetry_driver"]
    telemetry = telemetry_frame(source, SC_RACE, driver)

    distance = telemetry["distance_m"].to_list()
    assert all(b >= a for a, b in pairwise(distance))
    assert telemetry["time_s"].to_list() == sorted(telemetry["time_s"].to_list())


def test_telemetry_samples_are_tagged_with_their_lap(
    source: HistoricalSource, truth: dict[str, Any]
) -> None:
    expected = truth[truth_key(SC_RACE)]
    telemetry = telemetry_frame(source, SC_RACE, expected["telemetry_driver"])

    tagged = telemetry.filter(pl.col("lap_number").is_not_null())
    assert sorted(tagged["lap_number"].unique().to_list()) == list(
        range(1, expected["telemetry_laps"] + 1)
    )
    # Lap distance restarts each lap and never runs backwards inside one.
    for (lap,), group in tagged.group_by(["lap_number"], maintain_order=True):
        values = group.sort("ts_utc")["lap_distance_m"].to_list()
        assert values[0] == pytest.approx(0.0, abs=1e-9), lap
        assert all(b >= a for a, b in pairwise(values)), lap


def test_telemetry_is_empty_when_the_session_has_none(source: HistoricalSource) -> None:
    """A session with no telemetry yields an empty typed frame, not an
    exception — a backtest over mixed sessions must not die on one."""
    telemetry = telemetry_frame(source, GREEN_RACE, "1")

    assert telemetry.is_empty()
    assert dict(telemetry.schema) == TELEMETRY_FRAME_SCHEMA


# ---------------------------------------------------------------------------
# assembly + degenerate inputs
# ---------------------------------------------------------------------------


def test_session_frames_resolves_the_circuit(source: HistoricalSource) -> None:
    """The catalog's event name is what maps a session to its circuit."""
    frames = session_frames(source, SC_RACE)

    assert frames.event_name == "Bahrain Grand Prix"
    assert frames.circuit is not None
    assert frames.circuit.circuit_id == "bahrain"
    assert frames.drivers == sorted(frames.laps["driver"].unique().to_list())


def test_session_frames_without_circuit_metadata(source: HistoricalSource) -> None:
    """We ship six circuits, not twenty-four. An unknown one is None, not a
    raise — an analytic falls back to the default pit loss."""
    frames = session_frames(source, GREEN_RACE)

    assert frames.event_name == "Saudi Arabian Grand Prix"
    assert frames.circuit is not None and frames.circuit.circuit_id == "jeddah"

    unknown = session_frames(source, GREEN_RACE, event_name="Nowhere Grand Prix")
    assert unknown.circuit is None


def test_empty_event_stream_yields_typed_empty_frames() -> None:
    key = SessionKey(year=2024, round=99, session_type=SessionType.RACE)
    empty = MemorySource([])

    laps = lap_frame(empty, key)
    assert laps.is_empty()
    assert dict(laps.schema) == LAP_FRAME_SCHEMA
    assert dict(stint_frame(empty, key).schema) == STINT_FRAME_SCHEMA


def test_events_from_another_session_are_ignored(source: HistoricalSource) -> None:
    """A frame builder handed a mixed stream must take only its own
    session's events — the whole point of keying every event."""
    both = list(source.events(SC_RACE)) + list(source.events(GREEN_RACE))

    laps = lap_frame_from_events(both, SC_RACE)
    assert set(laps["session_round"].to_list()) == {SC_RACE.round}
    assert laps.equals(lap_frame(source, SC_RACE))


def _laps_where(laps: pl.DataFrame, column: str) -> dict[str, list[int]]:
    hits = laps.filter(pl.col(column)).sort("driver", "lap_number")
    result: dict[str, list[int]] = {}
    for driver, lap_number in zip(hits["driver"], hits["lap_number"], strict=True):
        result.setdefault(str(driver), []).append(int(lap_number))
    return result


def test_a_stint_opened_before_the_first_lap_covers_the_race_from_the_start() -> None:
    """Live, a race's first set opens before the feed counts a lap, so its
    `StintStarted` has no start lap; every live lap of the 2026 Singapore
    GP had no stint number until a stint like that counted from the start."""
    from datetime import UTC, datetime, timedelta

    from f1_domain import Compound, LapCompleted, StintStarted

    key = SessionKey(year=2026, round=17, session_type=SessionType.RACE)
    t0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def envelope(seconds: float) -> dict[str, Any]:
        return {
            "session_key": key,
            "ts_utc": t0 + timedelta(seconds=seconds),
            "source_seq": 0,
            "provenance": Provenance.LIVE,
        }

    events = [
        StintStarted(**envelope(0), driver="18", stint_number=1, compound=Compound.SOFT),
        *(LapCompleted(**envelope(100 * lap), driver="18", lap_number=lap) for lap in range(1, 5)),
        StintStarted(
            **envelope(250), driver="18", stint_number=2, compound=Compound.MEDIUM, start_lap=3
        ),
    ]

    laps = lap_frame_from_events(events, key).sort("lap_number")

    assert laps["stint_number"].to_list() == [1, 1, 2, 2]
