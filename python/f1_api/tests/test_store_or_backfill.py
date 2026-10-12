"""Which copy serves a qualifying session the store and the backfill both
hold. The store wins, as for every session, except a finished qualifying
the store took in before the parser read Q parts (2026-10-12): its replay
would show an empty qualifying board, while the backfill rebuilds the parts."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

import f1_api.sessions as sessions_module
from f1_api.config import Settings
from f1_api.sessions import list_sessions, resolve_session
from f1_domain import DomainEvent, LapCompleted, QualifyingPartChanged, SessionKey, SessionType
from f1_kernel import MemorySource, SessionRef

OLD = SessionKey(year=2026, round=17, session_type=SessionType.QUALIFYING)
NEW = SessionKey(year=2026, round=16, session_type=SessionType.SPRINT_QUALIFYING)
RUNNING = SessionKey(year=2026, round=18, session_type=SessionType.QUALIFYING)
RACE = SessionKey(year=2026, round=17, session_type=SessionType.RACE)
TS = datetime(2026, 10, 10, 13, 30, tzinfo=UTC)


def _lap(key: SessionKey) -> DomainEvent:
    return LapCompleted(session_key=key, ts_utc=TS, source_seq=1, driver="1", lap_number=1)


class _Store(MemorySource):
    def sessions(self) -> list[SessionRef]:
        return [
            SessionRef(session_key=k, event_name="Grand Prix") for k in (OLD, NEW, RUNNING, RACE)
        ]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    catalog = {
        "format_version": 1,
        "sessions": {
            f"{k.year}_{k.round:02d}_{label}": {
                "acquired_at": "2026-10-12T00:00:00Z",
                "event_name": "Grand Prix",
                "round": k.round,
                "season": k.year,
                "session_type": label,
                "sources": ["fastf1"],
                "tables": {"laps": 1},
            }
            for k, label in (
                (OLD, "Qualifying"),
                (NEW, "Sprint_Qualifying"),
                (RUNNING, "Qualifying"),
                (RACE, "Race"),
            )
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(catalog), encoding="utf-8")
    store = _Store(
        [
            _lap(OLD),
            _lap(NEW),
            QualifyingPartChanged(session_key=NEW, ts_utc=TS, source_seq=2, part=1),
            _lap(RUNNING),
            _lap(RACE),
        ]
    )

    @contextmanager
    def fake_store(_: Settings) -> Generator[sessions_module._Store | None, None, None]:  # pyright: ignore[reportPrivateUsage]
        yield sessions_module._Store(source=store, live_keys=frozenset({RUNNING}))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(sessions_module, "_store_source", fake_store)
    return Settings(historical_root=tmp_path)


def test_an_old_finished_qualifying_comes_from_the_backfill(settings: Settings) -> None:
    by_key = {e.session_key: e.provenance for e in list_sessions(settings)}
    assert by_key[OLD] == "historical"
    with resolve_session(settings, OLD) as resolved:
        assert resolved is not None
        assert resolved.entry.provenance == "historical"


def test_a_qualifying_the_store_parsed_with_parts_stays_on_the_store(settings: Settings) -> None:
    assert {e.session_key: e.provenance for e in list_sessions(settings)}[NEW] == "live"
    with resolve_session(settings, NEW) as resolved:
        assert resolved is not None
        assert resolved.entry.provenance == "live"


def test_a_running_qualifying_and_every_race_stay_on_the_store(settings: Settings) -> None:
    by_key = {e.session_key: e for e in list_sessions(settings)}
    assert by_key[RUNNING].provenance == "live"
    assert by_key[RUNNING].live is True
    assert by_key[RACE].provenance == "live"
