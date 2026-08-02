"""Decoder for `DriverList`: who each racing number currently is.

Keyed by racing number as a string; PARTIAL like the timing topics — a
mid-session message may carry only `{"44": {"Line": 3}}` (a position-line
update) with none of the identity fields. Every field is therefore
optional, and `extra` preserves whatever keys we didn't model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int, as_str

_KNOWN_KEYS = frozenset(
    {"RacingNumber", "Tla", "FullName", "BroadcastName", "TeamName", "TeamColour", "Line"}
)


@dataclass(frozen=True, slots=True)
class DriverEntryDelta:
    """One driver's (partial) DriverList entry."""

    racing_number: str
    tla: str | None
    """Three-letter code, e.g. "HAM"."""
    full_name: str | None
    broadcast_name: str | None
    team_name: str | None
    team_colour: str | None
    """Hex RGB without '#', verbatim (e.g. "6CD3BF")."""
    line: int | None
    """Current timing-screen line (classification position)."""
    extra: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    """Unmodeled keys, verbatim — never throw away data."""


def decode_driver_list(payload: JsonValue) -> dict[str, DriverEntryDelta]:
    """Racing number -> partial entry. Non-dict values under a number key
    (the feed sometimes sends `"_kf": true` bookkeeping) are skipped."""
    body = as_dict(payload) or {}
    entries: dict[str, DriverEntryDelta] = {}
    for number, raw in body.items():
        entry = as_dict(raw)
        if entry is None:
            continue
        entries[number] = DriverEntryDelta(
            racing_number=as_str(entry.get("RacingNumber")) or number,
            tla=as_str(entry.get("Tla")),
            full_name=as_str(entry.get("FullName")),
            broadcast_name=as_str(entry.get("BroadcastName")),
            team_name=as_str(entry.get("TeamName")),
            team_colour=as_str(entry.get("TeamColour")),
            line=as_int(entry.get("Line")),
            extra={k: v for k, v in entry.items() if k not in _KNOWN_KEYS},
        )
    return entries
