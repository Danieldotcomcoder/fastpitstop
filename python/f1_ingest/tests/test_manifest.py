"""Tests for building a Manifest from the raw SessionInfo topic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from f1_ingest.manifest import SessionInfoError, build_manifest_from_session_info


def test_builds_manifest_from_real_session_info_sample(
    belgian_gp_session_info: dict[str, Any],
) -> None:
    manifest = build_manifest_from_session_info(
        belgian_gp_session_info,
        recorder_version="test-1.0",
        topics=["TimingData", "WeatherData"],
    )

    assert manifest.year == 2024
    assert manifest.grand_prix == "Belgian Grand Prix"
    assert manifest.session_type == "Race"
    assert manifest.recorder_version == "test-1.0"
    assert manifest.topics == ["TimingData", "WeatherData"]
    # StartDate "2024-07-28T15:00:00" is local track time; GmtOffset "02:00:00"
    # means UTC is 2 hours earlier.
    assert manifest.session_start_utc == datetime(2024, 7, 28, 13, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("gmt_offset", "expected_utc_hour"),
    [
        ("00:00:00", 15),
        ("02:00:00", 13),
        ("-05:00:00", 20),
    ],
)
def test_gmt_offset_sign_handling(
    gmt_offset: str, expected_utc_hour: int, belgian_gp_session_info: dict[str, Any]
) -> None:
    session_info = {**belgian_gp_session_info, "GmtOffset": gmt_offset}
    manifest = build_manifest_from_session_info(session_info, recorder_version="test", topics=[])
    assert manifest.session_start_utc.hour == expected_utc_hour % 24


def test_missing_gmt_offset_defaults_to_utc(belgian_gp_session_info: dict[str, Any]) -> None:
    session_info = dict(belgian_gp_session_info)
    del session_info["GmtOffset"]
    manifest = build_manifest_from_session_info(session_info, recorder_version="test", topics=[])
    assert manifest.session_start_utc == datetime(2024, 7, 28, 15, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "broken_session_info",
    [
        {},
        {"Meeting": {}, "Type": "Race", "StartDate": "2024-07-28T15:00:00"},
        {"Meeting": {"Name": "X"}, "StartDate": "2024-07-28T15:00:00"},
        {"Meeting": {"Name": "X"}, "Type": "Race", "StartDate": "not-a-date"},
    ],
)
def test_malformed_session_info_raises(broken_session_info: dict[str, object]) -> None:
    with pytest.raises(SessionInfoError):
        build_manifest_from_session_info(broken_session_info, recorder_version="test", topics=[])
