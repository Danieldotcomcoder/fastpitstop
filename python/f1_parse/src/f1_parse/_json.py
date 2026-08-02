"""Tolerant coercion helpers over `pydantic.JsonValue`.

The feed's payloads are stringly-typed and shape-shifting (numbers arrive
as strings, containers arrive empty, fields vanish). Every helper here
returns `None` for "absent or not coercible" instead of raising, so
decoders read as straight-line field extraction and the pipeline never
crashes on a malformed value (the value is just dropped, and the raw
record is still preserved upstream in the recording).
"""

from __future__ import annotations

from pydantic import JsonValue


def as_dict(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def as_list(value: JsonValue | None) -> list[JsonValue] | None:
    return value if isinstance(value, list) else None


def as_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def as_float(value: JsonValue | None) -> float | None:
    """Coerce a number-or-numeric-string to float. Bools are NOT numbers
    here (JSON `true` is never a measurement); empty strings are absent.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def as_int(value: JsonValue | None) -> int | None:
    """Like `as_float` but only for integral values — a fractional float
    or non-integer string yields None rather than a silent truncation.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def as_bool_flag(value: JsonValue | None) -> bool | None:
    """The feed encodes booleans as actual bools, 0/1 ints, or "0"/"1"
    strings depending on topic and season. Normalize all three."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        text = value.strip()
        if text in {"0", "1"}:
            return text == "1"
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
    return None
