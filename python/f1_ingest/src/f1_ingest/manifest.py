"""Builds an `f1_recording.Manifest` from the raw `SessionInfo` topic.

`SessionInfo`'s shape is not officially documented. The fields used here
are cross-checked against a real capture (Belgian GP 2024, via
`https://livetiming.formula1.com/static/.../SessionInfo.json`):

    {
        "Meeting": {"Name": "Belgian Grand Prix", ...},
        "ArchiveStatus": {"Status": "Complete"},
        "Key": 9574,
        "Type": "Race",
        "Name": "Race",
        "StartDate": "2024-07-28T15:00:00",
        "EndDate": "2024-07-28T17:00:00",
        "GmtOffset": "02:00:00",
        "Path": "2024/2024-07-28_Belgian_Grand_Prix/2024-07-28_Race/"
    }

This can only be fully validated against a live capture (see the reality
check in `f1_ingest`'s package docstring) — if F1 changes this shape, this
is the one place that needs updating.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from f1_recording import Manifest

SESSION_INFO_TOPIC = "SessionInfo"


class SessionInfoError(ValueError):
    """Raised when the `SessionInfo` snapshot is missing or malformed."""


def build_manifest_from_session_info(
    session_info: Mapping[str, Any],
    *,
    recorder_version: str,
    topics: Sequence[str],
) -> Manifest:
    """Build a Manifest from the raw `SessionInfo` topic value."""
    try:
        meeting_name = session_info["Meeting"]["Name"]
        session_type = session_info["Type"]
        start_date = session_info["StartDate"]
    except (KeyError, TypeError) as exc:
        raise SessionInfoError(f"SessionInfo is missing expected field: {exc}") from exc

    gmt_offset = session_info.get("GmtOffset", "00:00:00")

    try:
        local_start = datetime.fromisoformat(start_date)
    except ValueError as exc:
        raise SessionInfoError(f"SessionInfo.StartDate is not a valid datetime: {exc}") from exc

    start_utc = (local_start - _parse_gmt_offset(gmt_offset)).replace(tzinfo=UTC)

    return Manifest(
        year=local_start.year,
        grand_prix=meeting_name,
        session_type=session_type,
        session_start_utc=start_utc,
        recorder_version=recorder_version,
        topics=list(topics),
    )


def _parse_gmt_offset(value: str) -> timedelta:
    """Parse a `"[-]HH:MM:SS"` offset string, as seen in `SessionInfo.GmtOffset`."""
    sign = -1 if value.startswith("-") else 1
    unsigned = value.removeprefix("-").removeprefix("+")
    try:
        hours, minutes, seconds = (int(part) for part in unsigned.split(":"))
    except ValueError as exc:
        raise SessionInfoError(f"SessionInfo.GmtOffset is not '[-]HH:MM:SS': {value!r}") from exc
    return sign * timedelta(hours=hours, minutes=minutes, seconds=seconds)
