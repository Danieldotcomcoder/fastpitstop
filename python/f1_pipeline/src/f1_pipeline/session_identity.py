"""Which session is this? Ask the feed.

`f1-pipeline live` used to be told its `SessionKey` on the command line, so a
deployment was pinned to one race: production spent the week after the 2026
Spanish GP still configured for `--year 2026 --round 14`, and would have
ingested the next race under the wrong key or not at all.

The subscribe snapshot already names the session — season, championship round
(`Meeting.Number`) and type — so this reads it. Explicit overrides still win,
for the rare case where the feed is wrong or a session must be re-keyed by
hand.
"""

from __future__ import annotations

from typing import Any

from f1_domain import SessionKey, SessionType, session_type_from_label
from f1_parse.session_info import decode_session_info

SESSION_INFO_TOPIC = "SessionInfo"


class SessionIdentityError(ValueError):
    """The snapshot does not name a session, and no override supplied one."""


def session_key_from_snapshot(
    snapshot: Any,
    *,
    year: int | None = None,
    round_number: int | None = None,
    session_type: str | None = None,
) -> SessionKey:
    """Derive this session's `SessionKey` from a subscribe snapshot.

    Every part is independently overridable, so a single wrong field on the
    feed does not force the whole key to be hand-specified.

    Raises `SessionIdentityError` rather than guessing: ingesting a race
    under the wrong key silently poisons the event log, and the log is the
    source of truth everything else is rebuilt from.
    """
    info = None
    # `snapshot` is whatever the socket handed us (Any), so reach for the
    # topic without narrowing to a dict pyright can only see as unknown-keyed.
    try:
        raw: Any = snapshot.get(SESSION_INFO_TOPIC)
    except AttributeError:
        raw = None
    if raw is not None:
        info = decode_session_info(raw)

    resolved_year = year
    if resolved_year is None and info is not None and info.start_date is not None:
        resolved_year = info.start_date.year

    resolved_round = round_number
    if resolved_round is None and info is not None:
        resolved_round = info.meeting_number

    resolved_type: SessionType | None = None
    if session_type is not None:
        resolved_type = session_type_from_label(session_type)
    elif info is not None:
        # `Name` first ("Practice 1", "Sprint Qualifying"): `Type` is the
        # coarser field and collapses all three practices into "Practice".
        for label in (info.session_name, info.session_type):
            if label is None:
                continue
            candidate = session_type_from_label(label)
            if candidate is not SessionType.UNKNOWN:
                resolved_type = candidate
                break

    missing = [
        name
        for name, value in (
            ("year", resolved_year),
            ("round", resolved_round),
            ("session_type", resolved_type),
        )
        if value is None
    ]
    if missing:
        raise SessionIdentityError(
            "the subscribe snapshot did not identify this session "
            f"({', '.join(missing)} unknown) — pass --year/--round/--session-type"
        )
    assert resolved_year is not None and resolved_round is not None
    assert resolved_type is not None
    return SessionKey(year=resolved_year, round=resolved_round, session_type=resolved_type)


__all__ = ["SessionIdentityError", "session_key_from_snapshot"]
