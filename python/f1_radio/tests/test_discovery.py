"""`season_session_keys`/`season_team_radio_events`: reading `f1_backfill`'s
own catalog to find which sessions to pull `TeamRadioClip` events from, and
degrading gracefully (not crashing) when the catalog outruns what's actually
on disk."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from f1_backfill.catalog import CATALOG_FILENAME, Catalog
from f1_domain import SessionType
from f1_radio.discovery import season_session_keys, season_team_radio_events


def _catalog_with(entries: list[tuple[int, int, str, str]]) -> Catalog:
    from f1_backfill.schedule import SessionKey as BackfillSessionKey

    catalog = Catalog()
    for season, round_no, session_type, event_name in entries:
        catalog.record(
            BackfillSessionKey(
                season=season,
                round=round_no,
                event_name=event_name,
                session_type=session_type,
                location="Sakhir",
            ),
            sources=["fastf1"],
            tables={"team_radio": 3},
            acquired_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
    return catalog


def test_returns_empty_list_when_no_catalog_exists(tmp_path: Path) -> None:
    assert season_session_keys(tmp_path, 2024) == []
    assert list(season_team_radio_events(tmp_path, 2024)) == []


def test_reads_session_keys_for_the_requested_season_only(tmp_path: Path) -> None:
    catalog = _catalog_with(
        [
            (2024, 1, "Race", "Bahrain Grand Prix"),
            (2024, 2, "Qualifying", "Saudi Arabian Grand Prix"),
            (2023, 1, "Race", "Bahrain Grand Prix"),
        ]
    )
    catalog.save(tmp_path / CATALOG_FILENAME)

    keys = season_session_keys(tmp_path, 2024)

    assert [(k.year, k.round, k.session_type) for k in keys] == [
        (2024, 1, SessionType.RACE),
        (2024, 2, SessionType.QUALIFYING),
    ]


def test_round_filter_restricts_to_one_round(tmp_path: Path) -> None:
    catalog = _catalog_with(
        [
            (2024, 1, "Race", "Bahrain Grand Prix"),
            (2024, 2, "Race", "Saudi Arabian Grand Prix"),
        ]
    )
    catalog.save(tmp_path / CATALOG_FILENAME)

    keys = season_session_keys(tmp_path, 2024, round_filter=2)

    assert [(k.year, k.round) for k in keys] == [(2024, 2)]


def test_unrecognized_session_type_label_maps_to_unknown(tmp_path: Path) -> None:
    catalog = _catalog_with([(2024, 1, "Some New Session", "Bahrain Grand Prix")])
    catalog.save(tmp_path / CATALOG_FILENAME)

    [key] = season_session_keys(tmp_path, 2024)
    assert key.session_type == SessionType.UNKNOWN


def test_a_catalog_entry_with_no_parquet_on_disk_is_skipped_not_raised(tmp_path: Path) -> None:
    # Catalog says this session exists, but no season=/round=/session=
    # directory was ever written under tmp_path -- e.g. the backfill output
    # was moved. Discovery must degrade, not crash.
    catalog = _catalog_with([(2024, 1, "Race", "Bahrain Grand Prix")])
    catalog.save(tmp_path / CATALOG_FILENAME)

    assert list(season_team_radio_events(tmp_path, 2024)) == []
