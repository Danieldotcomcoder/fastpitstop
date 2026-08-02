"""Connection management via environment variables.

`F1_STORE_DSN` is the one knob: a libpq connection string (or URL) pointing
at the TimescaleDB instance (the local one is defined in
`docker-compose.yml`). If it's unset, psycopg falls back to the standard
libpq `PG*` environment variables (`PGHOST`, `PGDATABASE`, ...), so the
usual Postgres tooling conventions just work.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import TupleRow

DSN_ENV_VAR = "F1_STORE_DSN"

# Matches the service in docker-compose.yml — handy as a default for local
# development, never used when F1_STORE_DSN or PG* env vars are set.
DEFAULT_LOCAL_DSN = "postgresql://pitwall:pitwall@localhost:5432/pitwall"


def dsn_from_env() -> str | None:
    """The configured DSN, or None to let libpq's PG* env vars decide."""
    return os.environ.get(DSN_ENV_VAR)


def connect(dsn: str | None = None, *, autocommit: bool = False) -> psycopg.Connection[TupleRow]:
    """Open a connection. `dsn` overrides the environment; otherwise use
    `F1_STORE_DSN`, otherwise libpq's own PG* defaults."""
    resolved = dsn if dsn is not None else dsn_from_env()
    if resolved is not None:
        return psycopg.connect(resolved, autocommit=autocommit)
    return psycopg.connect(autocommit=autocommit)
