"""Pydantic models for the f1_recording on-disk format.

This module defines the CONTRACT between the recorder and the replayer (see
CLAUDE.md's "Hard architectural invariants"). It knows nothing about F1
domain semantics beyond raw topic names — parsing payloads into laps,
stints, gaps, etc. is Phase 1's job. Payloads are stored exactly as
received, including the base64 string for compressed `.z` topics.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

FORMAT_VERSION = 1
"""Bump when the on-disk format changes in a way readers must handle explicitly."""

MANIFEST_FILENAME = "manifest.json"
SUBSCRIBE_FILENAME = "subscribe.json"
STREAM_FILENAME = "stream.jsonl"


class Manifest(BaseModel):
    """Session-level metadata for one recording, written once at creation time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = FORMAT_VERSION
    year: int
    grand_prix: str
    session_type: str
    session_start_utc: datetime
    recorder_version: str
    topics: list[str]


class RawRecord(BaseModel):
    """One line of stream.jsonl: a single raw message exactly as received.

    Immutable. `payload` is the raw JSON value as received — for compressed
    `.z` topics (e.g. `CarData.z`, `Position.z`) that means the base64
    string itself, never decoded. Use `decode_z` (read-side only) to inflate
    those.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=0)
    ts_wall: float
    ts_mono: int
    topic: str
    payload: JsonValue
