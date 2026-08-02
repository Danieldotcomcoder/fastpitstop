"""Shared test fixtures for f1_ingest."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def belgian_gp_session_info() -> dict[str, Any]:
    """A real SessionInfo sample (Belgian GP 2024), cross-checked against
    https://livetiming.formula1.com/static/2024/2024-07-28_Belgian_Grand_Prix/2024-07-28_Race/SessionInfo.json
    """
    return {
        "Meeting": {
            "Key": 1242,
            "Name": "Belgian Grand Prix",
            "OfficialName": "FORMULA 1 ROLEX BELGIAN GRAND PRIX 2024",
            "Location": "Spa-Francorchamps",
            "Country": {"Key": 16, "Code": "BEL", "Name": "Belgium"},
            "Circuit": {"Key": 7, "ShortName": "Spa-Francorchamps"},
        },
        "ArchiveStatus": {"Status": "Complete"},
        "Key": 9574,
        "Type": "Race",
        "Name": "Race",
        "StartDate": "2024-07-28T15:00:00",
        "EndDate": "2024-07-28T17:00:00",
        "GmtOffset": "02:00:00",
        "Path": "2024/2024-07-28_Belgian_Grand_Prix/2024-07-28_Race/",
    }


@pytest.fixture
def canned_subscribe_snapshot(belgian_gp_session_info: dict[str, Any]) -> dict[str, Any]:
    """A canned full-state dict shaped like a real subscribe completion result."""
    return {
        "SessionInfo": belgian_gp_session_info,
        "TrackStatus": {"Status": "1", "Message": "AllClear"},
        "WeatherData": {"AirTemp": "23.4", "TrackTemp": "31.2"},
        "DriverList": {"1": {"RacingNumber": "1", "FullName": "Max Verstappen"}},
    }
