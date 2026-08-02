"""Decoder for `TeamRadio`: pointers to driver-pit audio clips.

Snapshot form `{"Captures": [ ... ]}`, delta form `{"Captures": {"3":
{...}}}` (sparse-list encoding, like RaceControlMessages). `path` is kept
verbatim — it is feed-relative; resolving it to an absolute URL needs the
session's base path from SessionInfo, which is layer two's business.
Phase 4 (the radio corpus) ultimately hangs off these.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from f1_parse._json import as_dict, as_int, as_str
from f1_parse.times import parse_utc


@dataclass(frozen=True, slots=True)
class TeamRadioCapture:
    index: int | None
    """Position in the feed's accumulated capture list (delta form only)."""
    ts_utc: datetime | None
    racing_number: str | None
    path: str | None
    """Feed-relative audio path, verbatim."""


def decode_team_radio(payload: JsonValue) -> list[TeamRadioCapture]:
    body = as_dict(payload) or {}
    captures = body.get("Captures")
    result: list[TeamRadioCapture] = []
    if isinstance(captures, list):
        for item in captures:
            capture = _decode_capture(index=None, raw=item)
            if capture is not None:
                result.append(capture)
    elif isinstance(captures, dict):
        for key, item in captures.items():
            capture = _decode_capture(index=as_int(key), raw=item)
            if capture is not None:
                result.append(capture)
    return result


def _decode_capture(*, index: int | None, raw: JsonValue) -> TeamRadioCapture | None:
    body = as_dict(raw)
    if body is None:
        return None
    return TeamRadioCapture(
        index=index,
        ts_utc=parse_utc(as_str(body.get("Utc"))),
        racing_number=as_str(body.get("RacingNumber")),
        path=as_str(body.get("Path")),
    )
