"""Time parsing: every timestamp/duration shape the feed sends, normalized
in ONE place.

The feed mixes several shapes:
- ISO UTC instants, often with .NET's 7-digit fractional seconds and a
  trailing "Z" ("2024-03-02T15:43:21.9384291Z") — `parse_utc`.
- Lap/sector durations as bare strings: "1:23.456", "23.456", and rarely
  "1:02:03.456" — `parse_lap_time`. An EMPTY string means "no time set",
  never zero.
- Clock-style offsets like GmtOffset "03:00:00" / "-05:00:00" —
  `parse_clock_offset`.

All helpers return `None` for absent/blank/unparseable input rather than
raising — the feed is unofficial; a weird string must never take the
pipeline down. Everything datetime-shaped comes back timezone-aware UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_EXCESS_FRACTION_RE = re.compile(r"\.(\d{7,})")


def parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-ish instant to a timezone-aware UTC datetime.

    Handles the trailing "Z", .NET's 7-digit fractional seconds (truncated
    to microseconds), explicit offsets (converted to UTC), and naive
    strings (assumed to already BE UTC — that is what the feed sends).
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _EXCESS_FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_lap_time(value: str | None) -> timedelta | None:
    """Parse a lap/sector/gap duration string to a timedelta.

    Accepts "M:SS.fff", "SS.fff", "H:MM:SS.fff", and a leading "+" (gap
    strings arrive as "+1.234"). Empty/blank strings mean "no time" and
    return None — NOT zero; a 0.000 lap time would poison every
    fastest-lap comparison downstream. Unparseable input (including the
    lapped-car form "1 L") also returns None.
    """
    if value is None:
        return None
    text = value.strip().removeprefix("+")
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if seconds < 0 or minutes < 0 or hours < 0:
        return None
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def parse_clock_offset(value: str | None) -> timedelta | None:
    """Parse a signed "HH:MM:SS" clock offset (e.g. SessionInfo's
    GmtOffset "03:00:00", "-05:00:00") to a timedelta."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    negative = text.startswith("-")
    text = text.removeprefix("-").removeprefix("+")
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = float(parts[2])
    except ValueError:
        return None
    offset = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return -offset if negative else offset
