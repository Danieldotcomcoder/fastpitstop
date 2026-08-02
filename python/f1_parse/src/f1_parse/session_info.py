"""Decoder for `SessionInfo`: which session this even is.

Sent once on connect (in the subscribe snapshot) and again if the session
rolls over. This is where layer two will get the `SessionKey` and the
`Path` prefix that turns TeamRadio's relative audio paths into URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int, as_str
from f1_parse.times import parse_clock_offset, parse_utc


@dataclass(frozen=True, slots=True)
class SessionInfoDecoded:
    meeting_name: str | None
    """e.g. "Bahrain Grand Prix"."""
    meeting_key: int | None
    country: str | None
    circuit: str | None
    session_name: str | None
    """e.g. "Practice 1", "Qualifying", "Race", verbatim."""
    session_type: str | None
    """The feed's coarser Type field ("Practice", "Qualifying", "Race")."""
    session_key: int | None
    """The feed's own numeric key for this session (not our SessionKey)."""
    start_date: datetime | None
    """Local wall time at the circuit, made UTC via `gmt_offset` by layer two."""
    end_date: datetime | None
    gmt_offset: timedelta | None
    path: str | None
    """The static-content path prefix (team radio audio etc.), verbatim."""


def decode_session_info(payload: JsonValue) -> SessionInfoDecoded:
    body = as_dict(payload) or {}
    meeting = as_dict(body.get("Meeting")) or {}
    country = as_dict(meeting.get("Country")) or {}
    circuit = as_dict(meeting.get("Circuit")) or {}
    return SessionInfoDecoded(
        meeting_name=as_str(meeting.get("Name")),
        meeting_key=as_int(meeting.get("Key")),
        country=as_str(country.get("Name")),
        circuit=as_str(circuit.get("ShortName")),
        session_name=as_str(body.get("Name")),
        session_type=as_str(body.get("Type")),
        session_key=as_int(body.get("Key")),
        start_date=parse_utc(as_str(body.get("StartDate"))),
        end_date=parse_utc(as_str(body.get("EndDate"))),
        gmt_offset=parse_clock_offset(as_str(body.get("GmtOffset"))),
        path=as_str(body.get("Path")),
    )
