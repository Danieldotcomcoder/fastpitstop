"""The clean-lap filter, against ground truth.

The point of these tests is that the fixture KNOWS what it built — which
laps ran under the safety car, which driver sat in another's wake and for
how long, which lap is the planted lock-up — so the filter is checked
against reality rather than against its own output.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from typing import Any

import polars as pl
import pytest
from kernel_fixture import GREEN_RACE, SC_RACE, number, truth_key

from f1_domain import DomainEvent, SessionKey
from f1_kernel import (
    REASON_COLUMNS,
    HistoricalSource,
    KernelParams,
    SessionRef,
    annotate_laps,
    clean_lap_summary,
    clean_laps,
    lap_frame,
)


@pytest.fixture
def sc_laps(source: HistoricalSource) -> pl.DataFrame:
    return lap_frame(source, SC_RACE)


@pytest.fixture
def green_laps(source: HistoricalSource) -> pl.DataFrame:
    return lap_frame(source, GREEN_RACE)


# ---------------------------------------------------------------------------
# track status
# ---------------------------------------------------------------------------


def test_safety_car_laps_are_flagged(
    sc_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    """Every lap whose window overlapped the safety-car period is flagged,
    and no other lap is. Ground truth comes from the generator's own
    timeline, not from the kernel's reconstruction of it."""
    annotated = annotate_laps(sc_laps, params)
    expected = truth[truth_key(SC_RACE)]["sc_laps_by_driver"]

    actual = _laps_where(annotated, "is_sc")
    assert actual == {driver: list(laps) for driver, laps in expected.items()}
    # It really is a safety car, not some other status.
    assert not annotated["is_vsc"].any()
    assert not annotated["is_red"].any()


def test_safety_car_laps_are_excluded(sc_laps: pl.DataFrame, params: KernelParams) -> None:
    clean = clean_laps(sc_laps, params)

    assert not clean["is_sc"].any()
    assert clean.height < sc_laps.height


def test_a_green_race_has_no_safety_car_laps(
    green_laps: pl.DataFrame, params: KernelParams
) -> None:
    annotated = annotate_laps(green_laps, params)

    assert not annotated["is_sc"].any()
    assert not annotated["is_vsc"].any()


def test_sector_yellow_laps_are_flagged(
    green_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    """A yellow in one sector slows everyone who passes it, so it is track
    state — but only for the laps it actually overlapped."""
    annotated = annotate_laps(green_laps, params)
    expected = truth[truth_key(GREEN_RACE)]["yellow_laps_by_driver"]

    assert _laps_where(annotated, "is_yellow") == {
        driver: list(laps) for driver, laps in expected.items()
    }


def test_driver_scoped_flags_do_not_affect_track_status(
    sc_laps: pl.DataFrame, params: KernelParams
) -> None:
    """The fixture contains a blue flag waved at one car. Blue flags say
    nothing about the track; folding them in would flag half a race."""
    annotated = annotate_laps(sc_laps, params)
    lap3 = annotated.filter(pl.col("lap_number") == 3)

    assert not lap3["is_yellow"].any()
    assert lap3["is_clean"].all()


# ---------------------------------------------------------------------------
# pit-adjacent laps
# ---------------------------------------------------------------------------


def test_every_pit_stop_flags_an_in_lap_and_an_out_lap(
    sc_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    annotated = annotate_laps(sc_laps, params)
    expected = truth[truth_key(SC_RACE)]

    assert _laps_where(annotated, "is_in_lap") == {
        driver: list(laps) for driver, laps in expected["pit_in_laps"].items()
    }
    assert _laps_where(annotated, "is_out_lap") == {
        driver: list(laps) for driver, laps in expected["pit_out_laps"].items()
    }
    assert not clean_laps(sc_laps, params)["is_in_lap"].any()


def test_lap_one_is_never_clean(sc_laps: pl.DataFrame, params: KernelParams) -> None:
    annotated = annotate_laps(sc_laps, params)

    assert annotated.filter(pl.col("lap_number") == 1)["is_lap1"].all()
    assert not clean_laps(sc_laps, params).filter(pl.col("lap_number") == 1).height


# ---------------------------------------------------------------------------
# dirty air
# ---------------------------------------------------------------------------


def test_dirty_air_laps_are_flagged(
    sc_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    """The driver planted 0.75s behind the car ahead for six laps is flagged
    for exactly those laps."""
    annotated = annotate_laps(sc_laps, params)
    expected = truth[truth_key(SC_RACE)]["dirty_air_laps"]

    assert _laps_where(annotated, "is_dirty_air") == {
        driver: list(laps) for driver, laps in expected.items()
    }


def test_dirty_air_threshold_is_a_parameter(sc_laps: pl.DataFrame) -> None:
    """Nothing reads a global constant: moving the threshold below the
    planted gap must un-flag those laps."""
    tight = KernelParams().with_overrides(dirty_air_threshold_s=0.5)
    loose = KernelParams().with_overrides(dirty_air_threshold_s=5.0)

    assert not annotate_laps(sc_laps, tight)["is_dirty_air"].any()
    assert annotate_laps(sc_laps, loose)["is_dirty_air"].sum() > 100


def test_dirty_air_is_unknown_without_enough_samples(
    sc_laps: pl.DataFrame, params: KernelParams
) -> None:
    """The leader has no car ahead, so its dirty-air state is UNKNOWN — and
    unknown must not silently read as excluded."""
    annotated = annotate_laps(sc_laps, params).filter(pl.col("driver") == "1")

    assert not annotated["dirty_air_known"].any()
    assert not annotated["is_dirty_air"].any()
    assert annotated["is_clean"].any()


# ---------------------------------------------------------------------------
# robust outliers
# ---------------------------------------------------------------------------


def test_the_planted_anomaly_is_flagged(
    sc_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    driver, lap_number, _added = truth[truth_key(SC_RACE)]["anomaly"]
    annotated = annotate_laps(sc_laps, params)

    flagged = annotated.filter(pl.col("is_anomalous"))
    assert flagged.height == 1
    assert flagged["driver"][0] == driver
    assert flagged["lap_number"][0] == lap_number


def test_safety_car_laps_do_not_hide_real_anomalies(
    sc_laps: pl.DataFrame, params: KernelParams, truth: dict[str, Any]
) -> None:
    """The reason the bound is estimated over candidate laps only: laps 25
    seconds off the pace would otherwise inflate the spread until a
    six-second lock-up looked normal.

    Proof by contradiction — a mean/stddev-style bound computed over ALL
    laps in the stint would not flag the planted anomaly.
    """
    driver, lap_number, _added = truth[truth_key(SC_RACE)]["anomaly"]
    stint = sc_laps.filter((pl.col("driver") == driver) & (pl.col("stint_number") == 2))

    naive_mean = stint["lap_time_s"].mean()
    naive_std = stint["lap_time_s"].std()
    anomaly_time = stint.filter(pl.col("lap_number") == lap_number)["lap_time_s"][0]
    assert naive_mean is not None and naive_std is not None
    assert abs(anomaly_time - naive_mean) < 3.5 * naive_std, "naive bound would miss it"

    assert annotate_laps(sc_laps, params).filter(
        (pl.col("driver") == driver) & (pl.col("lap_number") == lap_number)
    )["is_anomalous"][0]


def test_anomaly_detection_needs_enough_laps(sc_laps: pl.DataFrame) -> None:
    """With an implausibly high minimum, no group qualifies and nothing is
    flagged — and `anomaly_known` says why."""
    strict = KernelParams().with_overrides(outlier_min_laps=100)

    annotated = annotate_laps(sc_laps, strict)

    assert not annotated["anomaly_known"].any()
    assert not annotated["is_anomalous"].any()


def test_identical_lap_times_do_not_produce_anomalies(params: KernelParams) -> None:
    """A near-zero MAD would make the bound infinitely tight; the floor stops
    every ordinary tenth being called an anomaly."""
    frame = _synthetic_laps([90.0] * 10 + [90.05])

    annotated = annotate_laps(frame, params)

    assert not annotated["is_anomalous"].any()


def test_a_genuine_outlier_beyond_the_floor_is_flagged(params: KernelParams) -> None:
    frame = _synthetic_laps([90.0] * 10 + [95.0])

    annotated = annotate_laps(frame, params)

    assert annotated["is_anomalous"].sum() == 1
    assert annotated.filter(pl.col("is_anomalous"))["lap_time_s"][0] == 95.0


# ---------------------------------------------------------------------------
# the contract of annotate/clean
# ---------------------------------------------------------------------------


def test_clean_is_exactly_the_annotated_rows_with_no_reasons(
    sc_laps: pl.DataFrame, params: KernelParams
) -> None:
    annotated = annotate_laps(sc_laps, params)
    clean = clean_laps(sc_laps, params)

    assert clean.equals(annotated.filter(pl.col("is_clean")))
    assert clean["exclusion_reasons"].list.len().max() == 0
    for column in REASON_COLUMNS:
        assert not clean[column].any(), column


def test_annotate_keeps_every_lap_and_explains_each_exclusion(
    sc_laps: pl.DataFrame, params: KernelParams
) -> None:
    """Nothing is silently dropped: the UI needs the excluded laps AND why."""
    annotated = annotate_laps(sc_laps, params)

    assert annotated.height == sc_laps.height
    assert annotated.select(sc_laps.columns).equals(sc_laps)

    excluded = annotated.filter(~pl.col("is_clean"))
    assert number(excluded["exclusion_reasons"].list.len().min()) >= 1
    reasons: set[str] = set()
    for row in excluded["exclusion_reasons"].to_list():
        reasons.update(str(reason) for reason in row)
    assert reasons <= set(REASON_COLUMNS)
    assert {"is_lap1", "is_in_lap", "is_out_lap", "is_sc", "is_dirty_air"} <= reasons


def test_clean_lap_fraction_is_sensible(sc_laps: pl.DataFrame, params: KernelParams) -> None:
    """A sanity range, not an exact count: a race with one safety car and a
    stop each should still leave most laps usable. If this ever collapses,
    the filter has become too aggressive to fit anything on."""
    summary = clean_lap_summary(sc_laps, params)

    assert summary.height == 8
    assert number(summary["clean_fraction"].min()) > 0.4
    assert number(summary["clean_fraction"].max()) < 0.95
    assert summary["clean_laps"].sum() == clean_laps(sc_laps, params).height


def test_annotation_is_deterministic(sc_laps: pl.DataFrame, params: KernelParams) -> None:
    first = annotate_laps(sc_laps, params)
    second = annotate_laps(sc_laps, params)

    assert first.equals(second)
    assert first.write_ipc(None).getvalue() == second.write_ipc(None).getvalue()


def test_empty_frame_annotates_to_an_empty_frame(params: KernelParams) -> None:
    empty = lap_frame(_EmptySource(), SC_RACE)

    annotated = annotate_laps(empty, params)

    assert annotated.is_empty()
    assert set(REASON_COLUMNS) <= set(annotated.columns)
    assert clean_laps(empty, params).is_empty()


def test_a_frame_without_the_lap_schema_is_rejected_clearly(params: KernelParams) -> None:
    with pytest.raises(ValueError, match="missing required column"):
        annotate_laps(pl.DataFrame({"driver": ["1"]}), params)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _EmptySource:
    """A source with nothing in it — the degenerate case every builder must
    survive without special-casing."""

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        return iter(())

    def sessions(self) -> list[SessionRef]:
        return []


def _synthetic_laps(lap_times: list[float]) -> pl.DataFrame:
    """A minimal one-driver, one-stint frame for bound-behaviour tests."""
    from datetime import UTC, datetime, timedelta

    from f1_kernel.schemas import LAP_FRAME_SCHEMA

    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    rows: dict[str, list[object]] = {name: [] for name in LAP_FRAME_SCHEMA}
    clock = start
    for index, lap_time in enumerate(lap_times, start=2):  # start at lap 2: lap 1 is never clean
        end = clock + timedelta(seconds=lap_time)
        rows["session_year"].append(2024)
        rows["session_round"].append(1)
        rows["session_type"].append("race")
        rows["driver"].append("1")
        rows["lap_number"].append(index)
        rows["lap_start_utc"].append(clock)
        rows["lap_end_utc"].append(end)
        rows["lap_time_s"].append(lap_time)
        rows["sector1_s"].append(None)
        rows["sector2_s"].append(None)
        rows["sector3_s"].append(None)
        rows["is_personal_best"].append(False)
        rows["compound"].append("soft")
        rows["tyre_age"].append(index)
        rows["stint_number"].append(1)
        rows["position"].append(1)
        rows["gap_ahead_s"].append(None)
        rows["gap_ahead_min_s"].append(None)
        rows["gap_ahead_samples"].append(0)
        rows["gap_to_leader_s"].append(None)
        rows["track_status"].append("green")
        rows["track_status_flags"].append([])
        rows["is_pit_in"].append(False)
        rows["is_pit_out"].append(False)
        rows["provenance"].append("historical")
        clock = end
    return pl.DataFrame(rows, schema=LAP_FRAME_SCHEMA)


def _laps_where(annotated: pl.DataFrame, column: str) -> dict[str, list[int]]:
    hits = annotated.filter(pl.col(column)).sort("driver", "lap_number")
    result: dict[str, list[int]] = {}
    for driver, lap_number in zip(hits["driver"], hits["lap_number"], strict=True):
        result.setdefault(str(driver), []).append(int(lap_number))
    return result
