"""Decoder for `ExtrapolatedClock`: the session clock.

`{"Utc": "2026-10-10T13:30:00.997Z", "Remaining": "00:17:59",
"Extrapolating": true}`: the time left as of `Utc`, and whether it is
counting down from there. PARTIAL like the timing topics (a restated clock
often carries only `Utc` and `Remaining`), so every field is optional and
layer two keeps what it already knew. The feed sends a message when the
clock starts, stops or jumps, never once a second.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from f1_parse._json import as_bool_flag, as_dict, as_str


@dataclass(frozen=True, slots=True)
class ClockDecoded:
    utc: str | None
    """When `remaining` was true, verbatim."""
    remaining: str | None
    """"HH:MM:SS", verbatim."""
    extrapolating: bool | None


def decode_clock(payload: JsonValue) -> ClockDecoded:
    body = as_dict(payload) or {}
    return ClockDecoded(
        utc=as_str(body.get("Utc")),
        remaining=as_str(body.get("Remaining")),
        extrapolating=as_bool_flag(body.get("Extrapolating")),
    )
