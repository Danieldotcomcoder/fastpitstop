"""The live ingest must name its own session, or refuse to guess."""

from __future__ import annotations

from typing import Any

import pytest

from f1_domain import SessionType
from f1_pipeline.session_identity import SessionIdentityError, session_key_from_snapshot


def _snapshot(**overrides: Any) -> dict[str, Any]:
    """The shape the real feed sent at the 2026 Spanish GP, trimmed."""
    body: dict[str, Any] = {
        "Meeting": {
            "Key": 1294,
            "Name": "Spanish Grand Prix",
            "Number": 14,
            "Country": {"Key": 1, "Code": "ESP", "Name": "Spain"},
            "Circuit": {"Key": 153, "ShortName": "Madring"},
        },
        "Key": 11369,
        "Type": "Race",
        "Name": "Race",
        "StartDate": "2026-09-13T15:00:00",
        "EndDate": "2026-09-13T17:00:00",
        "GmtOffset": "02:00:00",
        "Path": "2026/2026-09-13_Spanish_Grand_Prix/2026-09-13_Race/",
    }
    body.update(overrides)
    return {"SessionInfo": body, "Heartbeat": {"Utc": "2026-09-13T13:10:54Z"}}


def test_reads_the_session_straight_off_the_feed() -> None:
    key = session_key_from_snapshot(_snapshot())
    assert (key.year, key.round, key.session_type) == (2026, 14, SessionType.RACE)


@pytest.mark.parametrize(
    ("name", "feed_type", "expected"),
    [
        ("Practice 1", "Practice", SessionType.PRACTICE_1),
        ("Practice 3", "Practice", SessionType.PRACTICE_3),
        ("Qualifying", "Qualifying", SessionType.QUALIFYING),
        ("Sprint", "Race", SessionType.SPRINT),
        ("Sprint Qualifying", "Qualifying", SessionType.SPRINT_QUALIFYING),
        ("Race", "Race", SessionType.RACE),
    ],
)
def test_every_session_of_a_weekend_is_identified(
    name: str, feed_type: str, expected: SessionType
) -> None:
    """`Name` has to win over `Type`: the feed collapses all three practices
    into Type="Practice", and a sprint into Type="Race"."""
    key = session_key_from_snapshot(_snapshot(Name=name, Type=feed_type))
    assert key.session_type is expected


def test_explicit_overrides_beat_the_feed() -> None:
    key = session_key_from_snapshot(
        _snapshot(), year=2025, round_number=9, session_type="qualifying"
    )
    assert (key.year, key.round, key.session_type) == (2025, 9, SessionType.QUALIFYING)


def test_a_single_override_does_not_force_the_others() -> None:
    key = session_key_from_snapshot(_snapshot(), round_number=99)
    assert (key.year, key.round, key.session_type) == (2026, 99, SessionType.RACE)


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param({}, id="no SessionInfo at all"),
        pytest.param({"SessionInfo": {}}, id="empty SessionInfo"),
        pytest.param(None, id="not a mapping"),
    ],
)
def test_refuses_to_guess_when_the_feed_does_not_say(snapshot: Any) -> None:
    """Ingesting under a wrong key silently poisons the append-only log,
    which is the source of truth everything else is rebuilt from. Failing
    loudly is the cheaper mistake."""
    with pytest.raises(SessionIdentityError):
        session_key_from_snapshot(snapshot)


def test_names_exactly_what_it_could_not_work_out() -> None:
    snapshot = _snapshot()
    del snapshot["SessionInfo"]["Meeting"]["Number"]
    with pytest.raises(SessionIdentityError, match="round"):
        session_key_from_snapshot(snapshot)


def test_an_unrecognised_session_name_is_not_silently_unknown() -> None:
    """A name we cannot map must fail, not quietly become SessionType.UNKNOWN
    and ingest a real race under an unusable key."""
    with pytest.raises(SessionIdentityError, match="session_type"):
        session_key_from_snapshot(_snapshot(Name="Track Parade", Type="Parade"))
