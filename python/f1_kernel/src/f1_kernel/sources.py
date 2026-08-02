"""Where events come from: the loading half of the data layer.

The frame builders are pure functions of a `DomainEvent` stream; this
module is what produces those streams, so that the purity holds. Three
sources, one protocol:

- `MemorySource` — events you already have. What tests and analytics-in-a-
  notebook use, and the reason nothing in the kernel needs a database.
- `HistoricalSource` — `f1_backfill` parquet, mapped onto events by
  `f1_historical_adapter`. The 2023-2025 backtesting corpus.
- `StoreSource` — the TimescaleDB event log via `f1_store.replay_events`.
  Optional: importing it requires `f1_store`, and every test runs without
  one, because per CLAUDE.md the DB is a sink, never a dependency.

All three answer `events(session_key, kinds=..., drivers=...)`. The filters
are a PUSHDOWN, not a different code path: the stream you get is exactly
the full stream restricted to those kinds/drivers, just without paying to
build the rest. A race's telemetry is millions of samples, and a lap frame
should not have to materialize them to throw them away.

One caveat specific to historical sources: `source_seq` there is a
synthetic dense ordinal assigned at build time (DATA_CONTRACTS.md), so
filtering changes the numbering. Nothing in the kernel reads `source_seq`
for anything but tie-breaking within a timestamp, and that ordering is
preserved — but do not persist a filtered historical stream and expect its
seqs to match an unfiltered one.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from f1_domain import DomainEvent, SessionKey, SessionType

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps psycopg optional
    import psycopg
    from psycopg.rows import TupleRow

CATALOG_FILENAME = "manifest.json"
DEFAULT_HISTORICAL_ROOT = Path("data/historical")

# Which parquet tables an event kind needs, so a kind filter can skip
# loading (and converting) tables nothing asked for.
_KIND_TABLES: dict[str, tuple[str, ...]] = {
    "lap_completed": ("laps",),
    "sector_completed": ("laps",),
    "pit_entry": ("laps",),
    "pit_exit": ("laps",),
    "stint_started": ("laps",),
    "position_changed": ("laps", "results"),
    "driver_list_updated": ("results",),
    "gap_updated": ("intervals",),
    "weather_sample": ("weather",),
    "race_control_message": ("race_control",),
    "team_radio_clip": ("team_radio",),
    "car_telemetry": ("car_telemetry",),
}

# Column holding the driver number in each parquet table, for driver pushdown.
_DRIVER_COLUMNS: dict[str, str] = {
    "laps": "DriverNumber",
    "results": "DriverNumber",
    "car_telemetry": "DriverNumber",
    "intervals": "driver_number",
    "team_radio": "driver_number",
}

# Kinds the lap/stint frames actually read. Handy as a `kinds=` argument.
LAP_FRAME_KINDS: frozenset[str] = frozenset(
    {
        "lap_completed",
        "stint_started",
        "pit_entry",
        "pit_exit",
        "position_changed",
        "gap_updated",
        "track_status_changed",
        "race_control_message",
    }
)


class _CatalogEntry(BaseModel):
    """One session in `f1_backfill`'s catalog. Only the fields the kernel
    reads are declared; `extra="ignore"` means a catalog that grows new
    bookkeeping does not break loading (the format is specced in
    DATA_CONTRACTS.md, "Historical backfill tables")."""

    model_config = ConfigDict(extra="ignore")

    season: int
    round: int
    session_type: str = ""
    event_name: str = ""
    tables: dict[str, int] = Field(default_factory=dict[str, int])


class _Catalog(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sessions: dict[str, _CatalogEntry] = Field(default_factory=dict[str, _CatalogEntry])


@dataclass(frozen=True, slots=True)
class SessionRef:
    """A session that exists in some source, with the metadata needed to
    pick it (and to find its circuit) without loading it."""

    session_key: SessionKey
    event_name: str | None = None
    directory: Path | None = None
    tables: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        key = self.session_key
        return f"{key.year} R{key.round:02d} {key.session_type.value}"


@runtime_checkable
class EventSource(Protocol):
    """Yields the `DomainEvent`s of a session. The one thing the kernel
    needs to know about where data lives."""

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        """Events for one session, in emission order. `kinds`/`drivers`
        restrict the stream; the result must equal the unfiltered stream
        filtered the same way."""
        ...

    def sessions(self) -> list[SessionRef]:
        """Every session this source can serve, sorted."""
        ...


class MemorySource:
    """An in-memory event stream — the simplest source, and the one that
    keeps the kernel testable with no database and no parquet."""

    def __init__(
        self,
        events: Iterable[DomainEvent],
        *,
        event_names: dict[SessionKey, str] | None = None,
    ) -> None:
        self._events: list[DomainEvent] = list(events)
        self._event_names = dict(event_names or {})

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        wanted_kinds = frozenset(kinds) if kinds is not None else None
        wanted_drivers = frozenset(drivers) if drivers is not None else None
        for event in self._events:
            if event.session_key != session_key:
                continue
            if wanted_kinds is not None and event.kind not in wanted_kinds:
                continue
            if wanted_drivers is not None and not _matches_driver(event, wanted_drivers):
                continue
            yield event

    def sessions(self) -> list[SessionRef]:
        keys = {event.session_key for event in self._events}
        return sorted(
            (SessionRef(session_key=key, event_name=self._event_names.get(key)) for key in keys),
            key=_sort_key,
        )


class HistoricalSource:
    """`f1_backfill` parquet under `data/historical/`, served as events via
    `f1_historical_adapter` — the same events the live pipeline emits, which
    is the whole point of the Phase 1 seam.

    Filters are pushed down to the parquet frames BEFORE the adapter builds
    events, so asking for one driver's telemetry converts one driver's rows
    rather than the field's.
    """

    def __init__(self, root: Path | str = DEFAULT_HISTORICAL_ROOT) -> None:
        self.root = Path(root)

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        from f1_historical_adapter.adapter import build_events
        from f1_historical_adapter.reader import SessionTables, load_session_tables

        wanted_kinds = frozenset(kinds) if kinds is not None else None
        loaded = load_session_tables(self.root, session_key)

        tables = dict(loaded.tables)
        if wanted_kinds is not None:
            needed = {table for kind in wanted_kinds for table in _KIND_TABLES.get(kind, ())}
            tables = {name: frame for name, frame in tables.items() if name in needed}
        if drivers is not None:
            tables = _filter_tables_by_driver(tables, frozenset(drivers))

        include_telemetry = wanted_kinds is None or "car_telemetry" in wanted_kinds
        stream = build_events(
            SessionTables(
                session_key=loaded.session_key,
                directory=loaded.directory,
                tables=tables,
                session_start_utc=loaded.session_start_utc,
            ),
            include_telemetry=include_telemetry,
        )
        for event in stream:
            if wanted_kinds is not None and event.kind not in wanted_kinds:
                continue
            yield event

    def sessions(self) -> list[SessionRef]:
        return list_historical_sessions(self.root)


class StoreSource:
    """The TimescaleDB event log, read back through `f1_store.replay_events`.

    The log is the source of truth (DATA_CONTRACTS.md), so reading it gives
    exactly what the pipeline emitted — including live sessions, which have
    no parquet equivalent. Requires `f1_store` and an open connection; the
    kernel never opens one itself.
    """

    def __init__(
        self,
        conn: psycopg.Connection[TupleRow],
        *,
        event_names: dict[SessionKey, str] | None = None,
    ) -> None:
        self._conn = conn
        self._event_names = dict(event_names or {})

    def events(
        self,
        session_key: SessionKey,
        *,
        kinds: Collection[str] | None = None,
        drivers: Collection[str] | None = None,
    ) -> Iterator[DomainEvent]:
        from f1_store import replay_events

        wanted_drivers = frozenset(drivers) if drivers is not None else None
        for event in replay_events(self._conn, session_key, kinds=kinds):
            if wanted_drivers is not None and not _matches_driver(event, wanted_drivers):
                continue
            yield event

    def sessions(self) -> list[SessionRef]:
        """Sessions present in the event log, newest championship round last."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT session_year, session_round, session_type FROM events "
                "ORDER BY session_year, session_round, session_type"
            )
            rows = cur.fetchall()
        refs: list[SessionRef] = []
        for year, round_no, session_type in rows:
            key = SessionKey(
                year=int(year),
                round=int(round_no),
                session_type=session_type_from_label(str(session_type)),
            )
            refs.append(SessionRef(session_key=key, event_name=self._event_names.get(key)))
        return sorted(refs, key=_sort_key)


def list_historical_sessions(
    root: Path | str = DEFAULT_HISTORICAL_ROOT,
    *,
    session_types: Collection[SessionType] | None = None,
    seasons: Collection[int] | None = None,
) -> list[SessionRef]:
    """Every historical session available on disk, sorted.

    The backtest entry point: "which races can I run this analytic over?".
    Reads `f1_backfill`'s catalog (`manifest.json`), which is also where the
    event name comes from — and the event name is what maps a session to its
    circuit metadata.

    Returns an empty list when the root or catalog is absent, rather than
    raising: a machine with no backfill yet is a normal state, and a
    backtest over zero sessions should report that, not crash.
    """
    catalog_path = Path(root) / CATALOG_FILENAME
    if not catalog_path.is_file():
        return []
    catalog = _Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))

    refs: list[SessionRef] = []
    for entry in catalog.sessions.values():
        key = SessionKey(
            year=entry.season,
            round=entry.round,
            session_type=session_type_from_label(entry.session_type),
        )
        if session_types is not None and key.session_type not in session_types:
            continue
        if seasons is not None and key.year not in seasons:
            continue
        refs.append(
            SessionRef(
                session_key=key,
                event_name=entry.event_name or None,
                tables=tuple(sorted(entry.tables)),
            )
        )
    return sorted(refs, key=_sort_key)


_SESSION_TYPE_ALIASES: dict[str, SessionType] = {
    "practice1": SessionType.PRACTICE_1,
    "practice2": SessionType.PRACTICE_2,
    "practice3": SessionType.PRACTICE_3,
    "qualifying": SessionType.QUALIFYING,
    "sprint": SessionType.SPRINT,
    "sprintqualifying": SessionType.SPRINT_QUALIFYING,
    "sprintshootout": SessionType.SPRINT_QUALIFYING,
    "race": SessionType.RACE,
}


def session_type_from_label(raw: str) -> SessionType:
    """Map a catalog/DB session-type string onto the domain enum.

    The catalog carries FastF1's spelling ("Practice 1", "Sprint Shootout")
    and the DB carries the enum's own value, so normalize both by stripping
    everything that varies between them.
    """
    normalized = raw.strip().casefold().replace(" ", "").replace("_", "").replace("-", "")
    return _SESSION_TYPE_ALIASES.get(normalized, SessionType.UNKNOWN)


def _sort_key(ref: SessionRef) -> tuple[int, int, str]:
    key = ref.session_key
    return (key.year, key.round, key.session_type.value)


def _matches_driver(event: DomainEvent, drivers: frozenset[str]) -> bool:
    """Whether an event concerns one of `drivers`. Events with no driver at
    all (weather, track status, race control at track scope) always pass —
    they are session-wide context every driver's frame needs."""
    driver = getattr(event, "driver", None)
    if driver is None:
        return True
    return driver in drivers


def _filter_tables_by_driver(
    tables: dict[str, pd.DataFrame], drivers: frozenset[str]
) -> dict[str, pd.DataFrame]:
    """Restrict each parquet table to `drivers` before events are built.

    Driver numbers are strings in the domain but can be ints or floats in
    parquet, so comparison is on the string form of each value.
    """
    filtered: dict[str, pd.DataFrame] = {}
    for name, frame in tables.items():
        column = _DRIVER_COLUMNS.get(name)
        if column is None or column not in frame.columns:
            filtered[name] = frame
            continue
        mask = frame[column].map(lambda value: _driver_str(value) in drivers)
        filtered[name] = frame[mask]
    return filtered


def _driver_str(value: object) -> str:
    """Driver number as the domain spells it ('44'), from parquet's int/
    float/str representations."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
