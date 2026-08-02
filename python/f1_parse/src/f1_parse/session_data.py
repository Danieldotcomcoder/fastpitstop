"""Decoder for `SessionData`: the session's status timeline.

The snapshot form is `{"StatusSeries": [ ... ]}` (a plain list); the delta
form is `{"StatusSeries": {"3": { ... }}}` — the feed's sparse-list
encoding, where the key is the entry's index in the accumulated series
(the same duality as RaceControlMessages). Each entry carries either a
`SessionStatus` ("Started", "Finished", "Finalised", ...) or a
`TrackStatus` ("AllClear", "Yellow", ...), both kept verbatim — which of
the two drives which domain event is layer two's call, not this layer's.

The topic's other key (`Series`, lap/quali-part markers) is not decoded
yet; the raw record preserves it upstream if we ever want it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int, as_str
from f1_parse.times import parse_utc


@dataclass(frozen=True, slots=True)
class StatusSeriesEntry:
    index: int | None
    """Position in the feed's accumulated series (delta form only)."""
    ts_utc: datetime | None
    session_status: str | None
    """e.g. "Inactive", "Started", "Aborted", "Finished", "Finalised", verbatim."""
    track_status: str | None
    """e.g. "AllClear", "Yellow", verbatim (the message form, not the numeric code)."""


@dataclass(frozen=True, slots=True)
class SessionDataDecoded:
    status_series: list[StatusSeriesEntry]


def decode_session_data(payload: JsonValue) -> SessionDataDecoded:
    body = as_dict(payload) or {}
    series = body.get("StatusSeries")
    entries: list[StatusSeriesEntry] = []
    if isinstance(series, list):
        for item in series:
            entry = _decode_entry(index=None, raw=item)
            if entry is not None:
                entries.append(entry)
    elif isinstance(series, dict):
        for key, item in series.items():
            entry = _decode_entry(index=as_int(key), raw=item)
            if entry is not None:
                entries.append(entry)
    return SessionDataDecoded(status_series=entries)


def _decode_entry(*, index: int | None, raw: JsonValue) -> StatusSeriesEntry | None:
    body = as_dict(raw)
    if body is None:
        return None
    return StatusSeriesEntry(
        index=index,
        ts_utc=parse_utc(as_str(body.get("Utc"))),
        session_status=as_str(body.get("SessionStatus")),
        track_status=as_str(body.get("TrackStatus")),
    )
