"""`clip_cache_path`: pure path arithmetic, deterministic in (session,
driver, captured_at) with no filesystem I/O and no dependence on whether the
file has actually been fetched yet."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from f1_domain import SessionKey, SessionType
from f1_radio.cache import clip_cache_path

_ROOT = Path("cache")
_KEY = SessionKey(year=2024, round=1, session_type=SessionType.RACE)
_TS = datetime(2024, 3, 2, 15, 0, 0, tzinfo=UTC)


def test_same_inputs_always_resolve_to_the_same_path() -> None:
    first = clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/a.mp3")
    second = clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/a.mp3")
    assert first == second


def test_different_drivers_or_timestamps_resolve_to_different_paths() -> None:
    base = clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/a.mp3")
    other_driver = clip_cache_path(_ROOT, _KEY, "1", _TS, "https://example.com/a.mp3")
    other_ts = clip_cache_path(
        _ROOT, _KEY, "44", datetime(2024, 3, 2, 15, 1, 0, tzinfo=UTC), "https://example.com/a.mp3"
    )
    assert base != other_driver
    assert base != other_ts


def test_suffix_is_derived_from_the_audio_url() -> None:
    path = clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/clip.wav")
    assert path.suffix == ".wav"


def test_missing_or_extensionless_url_falls_back_to_mp3() -> None:
    assert clip_cache_path(_ROOT, _KEY, "44", _TS, None).suffix == ".mp3"
    assert clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/clip").suffix == ".mp3"


def test_path_is_nested_under_session_then_round_and_session_type() -> None:
    path = clip_cache_path(_ROOT, _KEY, "44", _TS, "https://example.com/a.mp3")
    assert path.parent.parent == _ROOT / "2024"
    assert path.parent.name == "01_race"


def test_unknown_driver_and_captured_at_still_produce_a_valid_path() -> None:
    path = clip_cache_path(_ROOT, _KEY, None, None, "https://example.com/a.mp3")
    assert path.name.startswith("unknown_unknown")
