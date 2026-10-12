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

from collections.abc import Collection, Container, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from f1_domain import DomainEvent, SessionKey, SessionType
from f1_domain import session_type_from_label as session_type_from_label
from f1_kernel.positions import normalize_track, track_from_events

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
    "car_position": ("car_positions",),
}

# Column holding the driver number in each parquet table, for driver pushdown.
_DRIVER_COLUMNS: dict[str, str] = {
    "laps": "DriverNumber",
    "results": "DriverNumber",
    "car_telemetry": "DriverNumber",
    "car_positions": "DriverNumber",
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
        # Which venue: a live session's only route to its circuit file.
        "session_info_updated",
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
    location: str = ""
    """FastF1's schedule location ("Marina Bay"), recorded by `f1-backfill`."""
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
    circuit_name: str | None = None
    """The feed's venue short name ("Madring"), when the source knows it:
    the unambiguous way to a circuit file (see `circuit_for_session`)."""
    location: str | None = None
    """FastF1's schedule location, for a backfilled session: its venue when
    the feed's name is not known (a Grand Prix name can move venues)."""

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

    def position_track(self, session_key: SessionKey) -> pl.DataFrame | None:
        return track_from_events(self.events(session_key, kinds=["car_position"]))

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
        needed = (
            None
            if wanted_kinds is None
            else {table for kind in wanted_kinds for table in _KIND_TABLES.get(kind, ())}
        )
        loaded = load_session_tables(self.root, session_key, only=needed)

        tables = dict(loaded.tables)
        if drivers is not None:
            tables = _filter_tables_by_driver(tables, frozenset(drivers))

        stream = build_events(
            SessionTables(
                session_key=loaded.session_key,
                directory=loaded.directory,
                tables=tables,
                session_start_utc=loaded.session_start_utc,
            ),
            include_telemetry=wanted_kinds is None or "car_telemetry" in wanted_kinds,
            include_positions=wanted_kinds is None or "car_position" in wanted_kinds,
        )
        for event in stream:
            if wanted_kinds is not None and event.kind not in wanted_kinds:
                continue
            yield event

    def position_track(self, session_key: SessionKey) -> pl.DataFrame | None:
        """The session's `car_positions` table, read columnar.

        Looked up by key alone, whether or not the catalog lists the
        session: the same directory is where a live-ingested session's
        positions land once they are imported from F1's post-session
        archive (`f1-backfill --positions-only --uncatalogued`)."""
        from f1_historical_adapter.reader import session_directory

        path = session_directory(self.root, session_key) / "car_positions.parquet"
        if not path.is_file():
            return None
        table = pl.read_parquet(path, columns=["Date", "DriverNumber", "X", "Y"])
        return normalize_track(
            table.rename({"Date": "ts_utc", "DriverNumber": "driver", "X": "x", "Y": "y"})
        )

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

    def events_after(
        self,
        session_key: SessionKey,
        after: tuple[int, int] | None,
        *,
        kinds: Collection[str] | None = None,
    ) -> Iterator[tuple[DomainEvent, tuple[int, int]]]:
        """Events strictly after `after`, each paired with its own cursor.

        The incremental twin of `events`, for a follower that polls the same
        growing session over and over (`f1_api`'s live WebSocket). Not part
        of the `EventSource` protocol: a parquet or in-memory source has no
        equivalent cursor, and every analytic still uses `events`.
        """
        from f1_store import replay_events_with_cursor

        yield from replay_events_with_cursor(self._conn, session_key, kinds=kinds, after=after)

    def events_since(
        self,
        session_key: SessionKey,
        since: datetime | None,
        *,
        kinds: Collection[str] | None = None,
        seen: Container[tuple[int, int, datetime]] = (),
    ) -> Iterator[tuple[DomainEvent, tuple[int, int, datetime]]]:
        """Events at or after `since` in time order, skipping identities in
        `seen` before parsing them: how the live WebSocket follows a growing
        session (see `f1_store.replay_events_since` for why by time, not by
        sequence). Not part of the `EventSource` protocol, like
        `events_after`."""
        from f1_store import replay_events_since

        yield from replay_events_since(self._conn, session_key, kinds=kinds, since=since, seen=seen)

    def position_track(self, session_key: SessionKey) -> pl.DataFrame | None:
        """The `car_positions` hypertable for one session, bulk-read.

        COPY as CSV rather than row by row: a race is ~640k rows, and
        fetching them as Python tuples costs several times the memory of
        the frame they end up in. `None` when the session has none (every
        live session, while the feed withholds positions)."""
        sql = (
            "COPY (SELECT (extract(epoch FROM ts_utc) * 1000000)::bigint, driver, x, y "
            "FROM car_positions WHERE session_year = {year} AND session_round = {round} "
            "AND session_type = {type} AND x IS NOT NULL AND y IS NOT NULL "
            "ORDER BY ts_utc, driver) TO STDOUT (FORMAT csv)"
        )
        from psycopg import sql as psql

        query = psql.SQL(sql).format(
            year=psql.Literal(session_key.year),
            round=psql.Literal(session_key.round),
            type=psql.Literal(session_key.session_type.value),
        )
        buffer = bytearray()
        with self._conn.cursor() as cur, cur.copy(query) as copy:
            for chunk in copy:
                buffer += chunk
        if not buffer:
            return None
        frame = pl.read_csv(
            bytes(buffer),
            has_header=False,
            new_columns=["ts_us", "driver", "x", "y"],
            schema_overrides={"ts_us": pl.Int64, "driver": pl.String},
        )
        return normalize_track(
            frame.select(
                pl.from_epoch("ts_us", time_unit="us").alias("ts_utc"),
                "driver",
                "x",
                "y",
            )
        )

    def sessions(self) -> list[SessionRef]:
        """Sessions present in the event log, newest championship round last.

        Named from the log's own `session_info_updated` events when no name
        was supplied: a live session has no catalog entry, and before this
        it reached the API nameless and so could never find its circuit."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT session_year, session_round, session_type FROM events "
                "ORDER BY session_year, session_round, session_type"
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT DISTINCT ON (session_year, session_round, session_type) "
                "session_year, session_round, session_type, "
                "payload->>'meeting_name', payload->>'circuit_short_name' "
                "FROM events WHERE kind = 'session_info_updated' "
                "ORDER BY session_year, session_round, session_type, ts_utc DESC"
            )
            info_rows = cur.fetchall()
        info: dict[tuple[int, int, str], tuple[str | None, str | None]] = {
            (int(y), int(r), str(t)): (meeting, venue) for y, r, t, meeting, venue in info_rows
        }
        refs: list[SessionRef] = []
        for year, round_no, session_type in rows:
            key = SessionKey(
                year=int(year),
                round=int(round_no),
                session_type=session_type_from_label(str(session_type)),
            )
            meeting, venue = info.get((int(year), int(round_no), str(session_type)), (None, None))
            refs.append(
                SessionRef(
                    session_key=key,
                    event_name=self._event_names.get(key) or meeting,
                    circuit_name=venue,
                )
            )
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
                location=entry.location or None,
            )
        )
    return sorted(refs, key=_sort_key)


# `session_type_from_label` lives in f1_domain (the vocabulary both this
# package and f1_parse need); re-exported here so every existing caller and
# import site keeps working.


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
