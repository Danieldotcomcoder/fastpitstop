"""Session-key resolution: name/slug -> SessionType, and historical
partition discovery."""

from __future__ import annotations

from pathlib import Path

from f1_domain import SessionType
from f1_pipeline import discover_historical_sessions, session_key, session_type_from_name


def test_session_type_from_name_handles_names_and_slugs() -> None:
    assert session_type_from_name("Race") is SessionType.RACE
    assert session_type_from_name("Practice_1") is SessionType.PRACTICE_1
    assert session_type_from_name("Practice 3") is SessionType.PRACTICE_3
    assert session_type_from_name("Qualifying") is SessionType.QUALIFYING
    # Sprint quali was renamed across seasons; both spellings map.
    assert session_type_from_name("Sprint Shootout") is SessionType.SPRINT_QUALIFYING
    assert session_type_from_name("Sprint_Qualifying") is SessionType.SPRINT_QUALIFYING
    assert session_type_from_name("Nonsense") is SessionType.UNKNOWN


def test_session_key_accepts_string_or_enum() -> None:
    assert session_key(year=2024, round=1, session_type="race").session_type is SessionType.RACE
    key = session_key(year=2024, round=1, session_type=SessionType.QUALIFYING)
    assert (key.year, key.round, key.session_type) == (2024, 1, SessionType.QUALIFYING)


def _make_partition(root: Path, season: int, rnd: int, slug: str) -> None:
    (root / f"season={season}" / f"round={rnd:02d}" / f"session={slug}").mkdir(parents=True)


def test_discover_historical_sessions_scans_partitions(tmp_path: Path) -> None:
    _make_partition(tmp_path, 2024, 1, "Practice_1")
    _make_partition(tmp_path, 2024, 1, "Race")
    _make_partition(tmp_path, 2024, 2, "Qualifying")
    _make_partition(tmp_path, 2024, 2, "Mystery")  # unknown slug -> skipped
    _make_partition(tmp_path, 2023, 1, "Race")  # different season -> excluded

    keys = discover_historical_sessions(tmp_path, 2024)
    assert [(k.round, k.session_type) for k in keys] == [
        (1, SessionType.PRACTICE_1),
        (1, SessionType.RACE),
        (2, SessionType.QUALIFYING),
    ]

    only_round_2 = discover_historical_sessions(tmp_path, 2024, round_filter=2)
    assert [k.session_type for k in only_round_2] == [SessionType.QUALIFYING]


def test_discover_returns_empty_for_missing_season(tmp_path: Path) -> None:
    assert discover_historical_sessions(tmp_path, 1999) == []
