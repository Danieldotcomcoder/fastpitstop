"""The `EventSink` contract and its database-free implementations.

`EventSink` is what the Phase 1 pipeline writes to. Per CLAUDE.md the DB is
a *sink*, not a dependency of the transform, so the normalizer must run and
be fully testable with no database present — hence `NullSink` (discard) and
`MemorySink` (accumulate in RAM, with the exact same idempotency the DB
enforces). `PostgresSink` (see `postgres.py`) is the real one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from f1_domain import DomainEvent, SessionState
from f1_store.rows import TableRow, derive_rows
from f1_store.schema import DRIVERS_COLUMNS, IDENTITY_KEYS, SESSIONS_COLUMNS, TABLE_COLUMNS


def assign_event_indices(events: Iterable[DomainEvent]) -> list[tuple[DomainEvent, int]]:
    """Pair each event with its `event_index`: its position within the run
    of events sharing the same `source_seq`.

    Relies on the pipeline's contract that events arrive in emission order,
    so all events from one record are contiguous (they are — the state
    machine returns each record's events together, and records are applied
    in strictly increasing seq). Deterministic, so re-ingesting the same
    stream yields identical `(source_seq, event_index)` pairs — that is what
    makes the writes idempotent.
    """
    paired: list[tuple[DomainEvent, int]] = []
    current_seq: int | None = None
    index = 0
    for event in events:
        if event.source_seq != current_seq:
            current_seq = event.source_seq
            index = 0
        else:
            index += 1
        paired.append((event, index))
    return paired


@runtime_checkable
class EventSink(Protocol):
    """Receives `DomainEvent`s and persists them. Implementations must be
    idempotent: writing the same events twice leaves stored state
    unchanged (see `schema.IDENTITY_KEYS`)."""

    def write_events(self, events: Iterable[DomainEvent]) -> None:
        """Append events to the log and their typed derived rows. Events
        must arrive in pipeline emission order (see `assign_event_indices`)."""
        ...

    def write_state(self, state: SessionState) -> None:
        """Upsert the materialized current-state (`sessions` + `drivers`)
        from a `SessionState` projection. Idempotent by construction (an
        upsert of the same snapshot is a no-op)."""
        ...

    def flush(self) -> None:
        """Ensure everything buffered so far is durably written."""
        ...

    def close(self) -> None:
        """Flush and release resources. Safe to call more than once."""
        ...


class NullSink:
    """Discards everything. For pipelines run purely for their event stream
    (tests, dry runs) where nothing should be persisted."""

    def write_events(self, events: Iterable[DomainEvent]) -> None:
        for _ in events:
            pass  # drain generators so callers behave identically to a real sink

    def write_state(self, state: SessionState) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class MemorySink:
    """Accumulates rows in memory, deduplicated exactly as the DB would (by
    `schema.IDENTITY_KEYS`), so idempotency is testable without a database.

    `rows[table]` holds the deduplicated column-dicts for that table (incl.
    the `events` log); `events` holds the raw `DomainEvent`s actually
    accepted; `state` holds the latest `SessionState` written.
    """

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, object]]] = {table: [] for table in TABLE_COLUMNS}
        self.events: list[DomainEvent] = []
        self.state: SessionState | None = None
        self.written_rows: dict[str, int] = {}
        """Rows sent to the sink per table this run (pre-dedup), mirroring
        `PostgresSink.written_rows` so metrics read the same off either."""
        self._seen: dict[str, set[tuple[object, ...]]] = {table: set() for table in TABLE_COLUMNS}

    def write_events(self, events: Iterable[DomainEvent]) -> None:
        for event, index in assign_event_indices(events):
            accepted = False
            for row in derive_rows(event, index):
                self.written_rows[row.table] = self.written_rows.get(row.table, 0) + 1
                if self._accept(row):
                    accepted = True
            if accepted:
                self.events.append(event)

    def _accept(self, row: TableRow) -> bool:
        identity = tuple(row.values.get(col) for col in IDENTITY_KEYS[row.table])
        seen = self._seen[row.table]
        if identity in seen:
            return False
        seen.add(identity)
        self.rows[row.table].append(row.values)
        return True

    def write_state(self, state: SessionState) -> None:
        self.state = state

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def row_counts(self) -> dict[str, int]:
        """`{table: row_count}` for every non-empty table — mirrors what a
        `SELECT count(*)` per table would show."""
        return {table: len(rows) for table, rows in self.rows.items() if rows}


# Re-exported so callers can reference the materialized-state column order
# without reaching into `schema`.
__all__ = [
    "DRIVERS_COLUMNS",
    "SESSIONS_COLUMNS",
    "EventSink",
    "MemorySink",
    "NullSink",
    "assign_event_indices",
]
