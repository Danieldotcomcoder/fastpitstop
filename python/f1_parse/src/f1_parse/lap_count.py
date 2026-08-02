"""Decoder for `LapCount`: `{"CurrentLap": 12, "TotalLaps": 57}`.

Race/sprint only (the topic simply never arrives in practice/quali).
PARTIAL like the timing topics — a mid-race delta usually carries only
`CurrentLap` — so both fields are optional and layer two keeps whichever
it already knew.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int


@dataclass(frozen=True, slots=True)
class LapCountDecoded:
    current_lap: int | None
    """The lap the leader is currently ON (1-based)."""
    total_laps: int | None
    """Scheduled race distance in laps."""


def decode_lap_count(payload: JsonValue) -> LapCountDecoded:
    body = as_dict(payload) or {}
    return LapCountDecoded(
        current_lap=as_int(body.get("CurrentLap")),
        total_laps=as_int(body.get("TotalLaps")),
    )
