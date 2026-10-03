"""f1_store: Phase 1 persistence for the normalized domain model.

The DB is a SINK, not a dependency of the transform (CLAUDE.md): the
normalizer runs and is fully testable with no database present. This
package stores what the pipeline emits, two ways at once:

- the **append-only event log** (`events`) — the source of truth, every
  `DomainEvent` as canonical JSON, replayable via `replay_events`;
- **materialized tables** — typed relational rows Phase 2 queries directly
  (`laps`, `car_telemetry`, `car_positions`, `stints`, `pit_stops`,
  `race_control`, `weather`, `team_radio`) plus current-state
  (`sessions`, `drivers`) upserted from `SessionState`.

`EventSink` is the contract. `PostgresSink` (batched COPY into TimescaleDB,
idempotent via `ON CONFLICT DO NOTHING`) is the real one; `NullSink` and
`MemorySink` need no database. Schema lives in versioned SQL under
`migrations/`, applied by `apply_migrations` / the `f1-store-migrate` CLI.
"""

from f1_store.config import DEFAULT_LOCAL_DSN, DSN_ENV_VAR, connect, dsn_from_env
from f1_store.migrations import apply_migrations, load_migrations, split_statements
from f1_store.postgres import (
    DEFAULT_LIVE_WINDOW,
    TERMINAL_SESSION_STATUSES,
    EventIdentity,
    PostgresSink,
    live_session_keys,
    replay_events,
    replay_events_since,
    replay_events_with_cursor,
)
from f1_store.rows import TableRow, derive_rows
from f1_store.schema import IDENTITY_KEYS, TABLE_COLUMNS
from f1_store.sink import EventSink, MemorySink, NullSink, assign_event_indices

__all__ = [
    "DEFAULT_LIVE_WINDOW",
    "DEFAULT_LOCAL_DSN",
    "DSN_ENV_VAR",
    "IDENTITY_KEYS",
    "TABLE_COLUMNS",
    "TERMINAL_SESSION_STATUSES",
    "EventIdentity",
    "EventSink",
    "MemorySink",
    "NullSink",
    "PostgresSink",
    "TableRow",
    "apply_migrations",
    "assign_event_indices",
    "connect",
    "derive_rows",
    "dsn_from_env",
    "live_session_keys",
    "load_migrations",
    "replay_events",
    "replay_events_since",
    "replay_events_with_cursor",
    "split_statements",
]

__version__ = "0.0.1"
