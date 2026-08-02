"""Decoder for the `Lines`-shaped timing topics: TimingData, TimingAppData,
TimingStats.

These are the archetypal PARTIAL-DELTA topics: each message is a nested
dict containing ONLY changed fields, keyed by racing number as a STRING
("44", "1") under `Lines`. This decoder does NOT merge anything — layer
one is stateless. It splits the message into the per-driver deltas and
whatever session-level keys rode along (`SessionPart`, `Withheld`, ...),
both verbatim, so layer two can `deep_merge` each driver's delta into its
running snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from f1_parse._json import as_dict


@dataclass(frozen=True, slots=True)
class TimingLinesDelta:
    """One timing message, split but not interpreted."""

    lines: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    """Racing number -> that driver's partial delta, verbatim."""
    other: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    """Any top-level keys besides `Lines` (e.g. `SessionPart`), verbatim."""


def decode_timing(payload: JsonValue) -> TimingLinesDelta:
    body = as_dict(payload) or {}
    lines_raw = as_dict(body.get("Lines")) or {}
    lines: dict[str, JsonValue] = dict(lines_raw)
    other = {key: value for key, value in body.items() if key != "Lines"}
    return TimingLinesDelta(lines=lines, other=other)
