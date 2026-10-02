"""`PostgresSink`: the real event sink, and `replay_events`, which reads the
log back out in order.

Writes are BATCHED VIA COPY, never row-by-row — telemetry is millions of
rows per race and per-row INSERT would bury us. Because raw `COPY` cannot
express `ON CONFLICT DO NOTHING`, each flush copies into a per-table TEMP
staging table (no constraints, so the copy never conflicts) and then does a
single `INSERT ... SELECT ... ON CONFLICT DO NOTHING` into the real table.
That keeps COPY throughput AND makes re-ingesting a recording idempotent.

The materialized `sessions`/`drivers` tables are tiny (<=20 drivers) and
upserted with plain `ON CONFLICT DO UPDATE` — no COPY needed there.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator
from datetime import timedelta

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

from f1_domain import (
    DOMAIN_EVENT_ADAPTER,
    DomainEvent,
    DriverState,
    SessionKey,
    SessionState,
    SessionType,
)
from f1_store.rows import derive_rows
from f1_store.schema import DRIVERS_COLUMNS, SESSION_COLUMNS, SESSIONS_COLUMNS, TABLE_COLUMNS
from f1_store.sink import assign_event_indices

DEFAULT_BATCH_SIZE = 10_000


class PostgresSink:
    """An `EventSink` backed by TimescaleDB. Not thread-safe: one sink per
    connection per pipeline. Call `flush()`/`close()` (or use as a context
    manager) so the final partial batch is written."""

    def __init__(
        self, conn: psycopg.Connection[TupleRow], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> None:
        self._conn = conn
        self._batch_size = batch_size
        self._buffers: dict[str, list[tuple[object, ...]]] = {t: [] for t in TABLE_COLUMNS}
        self._buffered = 0
        self.written_rows: dict[str, int] = {}
        """Rows SENT to the sink per table this run (pre-dedup; a re-ingest
        re-counts them even though ON CONFLICT drops them). A metrics hook,
        not a persisted-row count."""

    def __enter__(self) -> PostgresSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def write_events(self, events: Iterable[DomainEvent]) -> None:
        for event, index in assign_event_indices(events):
            for row in derive_rows(event, index):
                columns = TABLE_COLUMNS[row.table]
                self._buffers[row.table].append(tuple(row.values[col] for col in columns))
                self._buffered += 1
                self.written_rows[row.table] = self.written_rows.get(row.table, 0) + 1
            if self._buffered >= self._batch_size:
                self.flush()

    def flush(self) -> None:
        if self._buffered == 0:
            return
        wrote = False
        with self._conn.cursor() as cur:
            for table, buffered_rows in self._buffers.items():
                if not buffered_rows:
                    continue
                _copy_upsert(cur, table, buffered_rows)
                buffered_rows.clear()
                wrote = True
        self._buffered = 0
        if wrote:
            self._conn.commit()

    def write_state(self, state: SessionState) -> None:
        with self._conn.cursor() as cur:
            self._upsert_session(cur, state)
            for number, driver in state.drivers.items():
                self._upsert_driver(cur, state.session_key, number, driver)
        self._conn.commit()

    def close(self) -> None:
        self.flush()

    # -- materialized-state upserts ---------------------------------------

    def _upsert_session(self, cur: psycopg.Cursor[TupleRow], state: SessionState) -> None:
        values = {
            **_session_values(state.session_key),
            "status": state.status,
            "current_lap": state.current_lap,
            "track_status": state.track_status.value if state.track_status is not None else None,
        }
        _upsert(cur, "sessions", SESSIONS_COLUMNS, SESSION_COLUMNS, values)

    def _upsert_driver(
        self, cur: psycopg.Cursor[TupleRow], key: SessionKey, number: str, driver: DriverState
    ) -> None:
        values = {
            **_session_values(key),
            "number": number,
            "code": driver.driver.code,
            "team": driver.driver.team,
            "position": driver.position,
            "last_lap_s": _seconds(driver.last_lap),
            "best_lap_s": _seconds(driver.best_lap),
            "current_compound": (
                driver.current_compound.value if driver.current_compound is not None else None
            ),
            "tyre_age": driver.tyre_age,
            "gap_to_leader_s": _seconds(driver.gap_to_leader),
            "laps_down": driver.laps_down,
            "interval_s": _seconds(driver.interval),
            "in_pit": driver.in_pit,
            "stint_number": driver.stint_number,
            "laps_completed": driver.laps_completed,
        }
        _upsert(cur, "drivers", DRIVERS_COLUMNS, (*SESSION_COLUMNS, "number"), values)


def _seconds(value: timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None


def _session_values(key: SessionKey) -> dict[str, object]:
    return {
        "session_year": key.year,
        "session_round": key.round,
        "session_type": key.session_type.value,
    }


def _copy_upsert(cur: psycopg.Cursor[TupleRow], table: str, rows: list[tuple[object, ...]]) -> None:
    """COPY `rows` into a temp staging table, then insert-on-conflict-nothing
    into the real one. Staging is dropped at the enclosing commit."""
    columns = TABLE_COLUMNS[table]
    col_ident = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    table_ident = sql.Identifier(table)
    stage_ident = sql.Identifier(f"_stage_{table}")

    cur.execute(
        sql.SQL(
            "CREATE TEMP TABLE IF NOT EXISTS {stage} (LIKE {table}) ON COMMIT DELETE ROWS"
        ).format(stage=stage_ident, table=table_ident)
    )
    copy_stmt = sql.SQL("COPY {stage} ({cols}) FROM STDIN").format(
        stage=stage_ident, cols=col_ident
    )
    with cur.copy(copy_stmt) as copy:
        for row in rows:
            copy.write_row(row)
    cur.execute(
        sql.SQL(
            "INSERT INTO {table} ({cols}) SELECT {cols} FROM {stage} ON CONFLICT DO NOTHING"
        ).format(table=table_ident, cols=col_ident, stage=stage_ident)
    )


def _upsert(
    cur: psycopg.Cursor[TupleRow],
    table: str,
    columns: tuple[str, ...],
    conflict: tuple[str, ...],
    values: dict[str, object],
) -> None:
    updates = [c for c in columns if c not in conflict]
    stmt = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT ({conflict}) DO UPDATE SET {assignments}, updated_at = now()"
    ).format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder(c) for c in columns),
        conflict=sql.SQL(", ").join(sql.Identifier(c) for c in conflict),
        assignments=sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c)) for c in updates
        ),
    )
    cur.execute(stmt, values)


def replay_events(
    conn: psycopg.Connection[TupleRow],
    session_key: SessionKey,
    *,
    kinds: Collection[str] | None = None,
    after: tuple[int, int] | None = None,
) -> Iterator[DomainEvent]:
    """Read the append-only log back out for one session, in original
    emission order, revalidating each payload into a `DomainEvent`.

    This is the proof that `events` is a genuine source of truth: the
    stream it yields reconstructs exactly what the pipeline emitted (order
    by `(source_seq, event_index)` — the deterministic emission order).

    `kinds` restricts the read to those event kinds (`DomainEvent.kind`
    values). The result is exactly the full stream filtered to those kinds,
    just without transferring the rest: a consumer that only wants laps
    should not have to pull a race's telemetry across the wire to drop it
    client-side.
    """
    conditions = [
        sql.SQL("session_year = %(year)s AND session_round = %(round)s AND session_type = %(type)s")
    ]
    params: dict[str, object] = {
        "year": session_key.year,
        "round": session_key.round,
        "type": session_key.session_type.value,
    }
    if kinds is not None:
        conditions.append(sql.SQL("kind = ANY(%(kinds)s)"))
        params["kinds"] = list(kinds)
    if after is not None:
        # Row-wise comparison on the SAME pair the ORDER BY uses, so "everything
        # after this cursor" means exactly "everything this caller has not been
        # handed yet" — no per-row bookkeeping, no overlap, no gap.
        conditions.append(sql.SQL("(source_seq, event_index) > (%(after_seq)s, %(after_index)s)"))
        params["after_seq"], params["after_index"] = after

    stmt = sql.SQL(
        "SELECT payload, source_seq, event_index FROM events WHERE {where} "
        "ORDER BY source_seq, event_index, ts_utc"
    ).format(where=sql.SQL(" AND ").join(conditions))
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        for row in cur:
            yield DOMAIN_EVENT_ADAPTER.validate_python(row[0])


def replay_events_with_cursor(
    conn: psycopg.Connection[TupleRow],
    session_key: SessionKey,
    *,
    kinds: Collection[str] | None = None,
    after: tuple[int, int] | None = None,
) -> Iterator[tuple[DomainEvent, tuple[int, int]]]:
    """`replay_events`, but each event is paired with its own
    `(source_seq, event_index)` cursor.

    A long-lived follower (the live WebSocket) needs to resume exactly where
    it stopped. Without this it has to re-read and re-validate the entire
    session on every poll: at one poll a second on a real race that is tens
    of thousands of rows rebuilt per second, per connected client, which is
    what pinned the API at 100% CPU during the 2026 Spanish GP.
    """
    conditions = [
        sql.SQL("session_year = %(year)s AND session_round = %(round)s AND session_type = %(type)s")
    ]
    params: dict[str, object] = {
        "year": session_key.year,
        "round": session_key.round,
        "type": session_key.session_type.value,
    }
    if kinds is not None:
        conditions.append(sql.SQL("kind = ANY(%(kinds)s)"))
        params["kinds"] = list(kinds)
    if after is not None:
        conditions.append(sql.SQL("(source_seq, event_index) > (%(after_seq)s, %(after_index)s)"))
        params["after_seq"], params["after_index"] = after

    stmt = sql.SQL(
        "SELECT payload, source_seq, event_index FROM events WHERE {where} "
        "ORDER BY source_seq, event_index, ts_utc"
    ).format(where=sql.SQL(" AND ").join(conditions))
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        for row in cur:
            yield DOMAIN_EVENT_ADAPTER.validate_python(row[0]), (int(row[1]), int(row[2]))


DEFAULT_LIVE_WINDOW = timedelta(minutes=10)
"""How recent the newest event must be for a session to count as in progress.

Generous on purpose. A live session emits timing updates continuously, so
the honest signal is "the log is still growing"; the window only has to
survive a red flag or a feed reconnect, both of which are minutes, not tens
of minutes. Erring long is the cheap direction: a session stays "live" for a
few extra minutes after the flag (it is still finalising), whereas erring
short would blank the dashboard mid-race.
"""

TERMINAL_SESSION_STATUSES = ("Ends", "Finalised")
"""Feed statuses that mean the session is definitively over.

Deliberately NOT a full denylist of the feed's vocabulary: "Inactive"
precedes a session, and "Aborted" is a red flag the session can resume from.
Matching only the unambiguous values keeps an unrecognised status falling
through to the freshness test rather than being guessed at.

"Finished" is not one of them. It ends every qualifying segment (2026
Azerbaijan qualifying: Started, Finished, Inactive, Started, Finished, ...),
so treating it as terminal would drop the dashboard at the end of Q1. At the
chequered flag it would also retire the race while a viewer on a broadcast
delay is still watching the last laps. "Finalised" follows once the results
are confirmed, about ten minutes later.
"""


def live_session_keys(
    conn: psycopg.Connection[TupleRow],
    *,
    within: timedelta = DEFAULT_LIVE_WINDOW,
) -> set[SessionKey]:
    """The sessions whose event log is still growing right now.

    "Present in the store" is NOT "in progress" — every session ever
    ingested stays in the log forever. Conflating the two made the 2026
    Spanish GP read as live for the whole following week: `/live` rendered
    a six-day-old race as though it were running, opened a `mode=live`
    WebSocket against it, and re-derived its 33k events (and narrated its
    anomalies at an LLM) for every visitor.

    Liveness is measured from EVENT time, not a row's write time. The feed
    keeps serving the previous session's snapshot between race weekends, so
    an idle ingest re-seeds and touches `sessions.updated_at` indefinitely;
    the event timestamps it writes stay correctly in the past. A terminal
    `sessions.status` disqualifies regardless, which retires a session
    promptly after the flag instead of waiting out the window.
    """
    stmt = sql.SQL(
        "SELECT e.session_year, e.session_round, e.session_type "
        "FROM events e "
        "LEFT JOIN sessions s ON s.session_year = e.session_year "
        "  AND s.session_round = e.session_round "
        "  AND s.session_type = e.session_type "
        "WHERE e.ts_utc >= now() - %(within)s "
        "  AND (s.status IS NULL OR NOT (s.status = ANY(%(terminal)s))) "
        "GROUP BY e.session_year, e.session_round, e.session_type"
    )
    params: dict[str, object] = {
        "within": within,
        "terminal": list(TERMINAL_SESSION_STATUSES),
    }
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        rows = cur.fetchall()
    return {
        SessionKey(
            year=int(year),
            round=int(round_no),
            session_type=SessionType(str(session_type)),
        )
        for year, round_no, session_type in rows
    }
