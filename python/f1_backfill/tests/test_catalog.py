"""Catalog: idempotency bookkeeping, persisted as manifest.json."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from f1_backfill.catalog import Catalog
from f1_backfill.schedule import SessionKey

_KEY = SessionKey(
    season=2024, round=1, event_name="Bahrain Grand Prix", session_type="Race", location="Sakhir"
)


def test_missing_catalog_file_loads_as_empty(tmp_path: Path) -> None:
    catalog = Catalog.load(tmp_path / "manifest.json")
    assert not catalog.has(_KEY)
    assert catalog.sessions == {}


def test_record_then_save_then_reload_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    catalog = Catalog.load(path)
    acquired_at = datetime(2024, 3, 1, tzinfo=UTC)

    catalog.record(
        _KEY, sources=["fastf1", "openf1"], tables={"laps": 1200}, acquired_at=acquired_at
    )
    catalog.save(path)

    reloaded = Catalog.load(path)
    assert reloaded.has(_KEY)
    entry = reloaded.sessions[_KEY.catalog_key]
    assert entry.season == 2024
    assert entry.round == 1
    assert entry.session_type == "Race"
    assert entry.sources == ["fastf1", "openf1"]
    assert entry.tables == {"laps": 1200}
    assert entry.acquired_at == acquired_at


def test_has_is_false_for_a_different_session(tmp_path: Path) -> None:
    catalog = Catalog.load(tmp_path / "manifest.json")
    catalog.record(_KEY, sources=["fastf1"], tables={}, acquired_at=datetime.now(UTC))

    other = SessionKey(
        season=2024, round=2, event_name="Saudi Arabian GP", session_type="Race", location="Jeddah"
    )
    assert not catalog.has(other)
    assert catalog.has(_KEY)
