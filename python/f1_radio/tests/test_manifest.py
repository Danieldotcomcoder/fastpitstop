"""`FetchManifest`: load/save round-trip and the `has()`/`record()` gate
`fetch_clips` builds its idempotency on."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from radio_data import KEY

from f1_radio.manifest import ClipEntry, ClipStatus, FetchManifest, clip_key

_NOW = datetime(2024, 3, 2, 16, 0, 0, tzinfo=UTC)


def _entry(**overrides: object) -> ClipEntry:
    fields: dict[str, object] = {
        "year": 2024,
        "round": 1,
        "session_type": "race",
        "driver": "44",
        "captured_at": _NOW,
        "audio_url": "https://example.com/a.mp3",
        "audio_path": "2024/01_race/44_x.mp3",
        "status": ClipStatus.FETCHED,
        "fetched_at": _NOW,
    }
    fields.update(overrides)
    return ClipEntry(**fields)  # type: ignore[arg-type]


def test_load_of_a_missing_file_returns_an_empty_manifest(tmp_path: Path) -> None:
    manifest = FetchManifest.load(tmp_path / "manifest.json")
    assert manifest.clips == {}


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = FetchManifest()
    manifest.record("k1", _entry())
    manifest.save(path)

    reloaded = FetchManifest.load(path)
    assert reloaded.clips == manifest.clips


def test_has_is_false_until_recorded() -> None:
    manifest = FetchManifest()
    assert not manifest.has("k1")
    manifest.record("k1", _entry())
    assert manifest.has("k1")


def test_record_overwrites_an_existing_key() -> None:
    manifest = FetchManifest()
    manifest.record("k1", _entry(status=ClipStatus.FAILED, audio_path=None, error="boom"))
    manifest.record("k1", _entry(status=ClipStatus.FETCHED))
    assert manifest.clips["k1"].status == ClipStatus.FETCHED
    assert manifest.clips["k1"].error is None


def test_clip_key_is_deterministic_and_distinguishes_clips() -> None:
    a = clip_key(KEY, "44", _NOW)
    b = clip_key(KEY, "44", _NOW)
    c = clip_key(KEY, "1", _NOW)
    assert a == b
    assert a != c
