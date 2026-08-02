"""Decoder for `TrackStatus`: `{"Status": "1", "Message": "AllClear"}`.

The numeric code is kept verbatim as `status_code`; mapping codes onto the
domain's `TrackStatusKind` is layer two's job (this layer stays free of
domain vocabulary). Known codes, for reference (FastF1's docs agree):
"1" green, "2" yellow, "4" safety car, "5" red, "6" VSC deployed,
"7" VSC ending.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from f1_parse._json import as_dict, as_str


@dataclass(frozen=True, slots=True)
class TrackStatusDecoded:
    status_code: str | None
    """The feed's numeric code, verbatim (e.g. "1", "6")."""
    message: str | None
    """e.g. "AllClear", "Yellow", "SCDeployed", verbatim."""


def decode_track_status(payload: JsonValue) -> TrackStatusDecoded:
    body = as_dict(payload) or {}
    return TrackStatusDecoded(
        status_code=as_str(body.get("Status")),
        message=as_str(body.get("Message")),
    )
