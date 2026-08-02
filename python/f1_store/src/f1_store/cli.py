"""`f1-store-migrate`: apply the schema migrations to the configured DB.

Reads `F1_STORE_DSN` (or libpq PG* env vars) — see `config`. Idempotent:
already-applied migrations are skipped, so it is safe to run on every
deploy / before every ingest.
"""

from __future__ import annotations

import argparse
import sys

from f1_store.config import DSN_ENV_VAR, connect
from f1_store.migrations import apply_migrations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply f1_store schema migrations.")
    parser.add_argument(
        "--dsn",
        default=None,
        help=f"libpq connection string; overrides ${DSN_ENV_VAR} and PG* env vars.",
    )
    args = parser.parse_args(argv)

    with connect(args.dsn) as conn:
        applied = apply_migrations(conn)

    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Schema already up to date; nothing to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
