"""Decoder for `RaceControlMessages`.

The snapshot form is `{"Messages": [ ... ]}` (a plain list); the delta
form is `{"Messages": {"5": { ... }}}` — the feed's sparse-list encoding,
where the key is the message's index in the accumulated list. Both fan
out to `RaceControlEntry` items; for the delta form the index is kept so
layer two can slot it into the right place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int, as_str
from f1_parse.times import parse_utc


@dataclass(frozen=True, slots=True)
class RaceControlEntry:
    index: int | None
    """Position in the feed's accumulated message list (delta form only)."""
    ts_utc: datetime | None
    category: str | None
    """e.g. "Flag", "SafetyCar", "Drs", "Other", verbatim."""
    message: str | None
    flag: str | None
    scope: str | None
    """e.g. "Track", "Sector", "Driver", verbatim."""
    racing_number: str | None
    sector: int | None
    lap: int | None


def decode_race_control(payload: JsonValue) -> list[RaceControlEntry]:
    body = as_dict(payload) or {}
    messages = body.get("Messages")
    entries: list[RaceControlEntry] = []
    if isinstance(messages, list):
        for item in messages:
            entry = _decode_entry(index=None, raw=item)
            if entry is not None:
                entries.append(entry)
    elif isinstance(messages, dict):
        for key, item in messages.items():
            entry = _decode_entry(index=as_int(key), raw=item)
            if entry is not None:
                entries.append(entry)
    return entries


def _decode_entry(*, index: int | None, raw: JsonValue) -> RaceControlEntry | None:
    body = as_dict(raw)
    if body is None:
        return None
    return RaceControlEntry(
        index=index,
        ts_utc=parse_utc(as_str(body.get("Utc"))),
        category=as_str(body.get("Category")),
        message=as_str(body.get("Message")),
        flag=as_str(body.get("Flag")),
        scope=as_str(body.get("Scope")),
        racing_number=as_str(body.get("RacingNumber")),
        sector=as_int(body.get("Sector")),
        lap=as_int(body.get("Lap")),
    )
