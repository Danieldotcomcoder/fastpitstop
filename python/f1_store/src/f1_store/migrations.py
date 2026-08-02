"""Applies the checked-in SQL migrations under `migrations/`.

Plain versioned `.sql` files (no alembic — one linear history, applied in
filename order, each recorded in `schema_migrations` so re-running is a
no-op). psycopg's extended query protocol allows only one statement per
`execute`, so `split_statements` splits each file into individual
statements — dollar-quote-, string-, and comment-aware, and unit-tested
without a database.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import TupleRow

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements on top-level `;`.

    Aware of `--` line comments, single/double-quoted literals, and
    `$tag$...$tag$` dollar-quoted blocks, so a semicolon inside any of those
    is not treated as a statement boundary. Blank/comment-only fragments are
    dropped.
    """
    statements: list[str] = []
    buffer: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        # Line comment: skip to end of line.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue

        # Dollar-quoted block: $tag$ ... $tag$ (tag may be empty).
        if ch == "$":
            close = _dollar_tag_end(sql, i)
            if close is not None:
                tag = sql[i:close]
                end = sql.find(tag, close)
                if end == -1:
                    buffer.append(sql[i:])
                    break
                buffer.append(sql[i : end + len(tag)])
                i = end + len(tag)
                continue

        # Quoted string literal.
        if ch in "'\"":
            end = _string_literal_end(sql, i, ch)
            buffer.append(sql[i:end])
            i = end
            continue

        if ch == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue

        buffer.append(ch)
        i += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _dollar_tag_end(sql: str, start: int) -> int | None:
    """If a dollar-quote tag opens at `start`, return the index just past
    its closing `$`; else None."""
    i = start + 1
    n = len(sql)
    while i < n and (sql[i].isalnum() or sql[i] == "_"):
        i += 1
    if i < n and sql[i] == "$":
        return i + 1
    return None


def _string_literal_end(sql: str, start: int, quote: str) -> int:
    """Return the index just past the closing quote of the literal opening
    at `start`, honoring doubled-quote escapes (`''`)."""
    i = start + 1
    n = len(sql)
    while i < n:
        if sql[i] == quote:
            if i + 1 < n and sql[i + 1] == quote:  # escaped quote
                i += 2
                continue
            return i + 1
        i += 1
    return n


def load_migrations() -> list[tuple[str, str]]:
    """`(version, sql)` for every migration file, in filename order.
    `version` is the filename stem (e.g. `001_initial`)."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [(path.stem, path.read_text(encoding="utf-8")) for path in files]


def applied_versions(conn: psycopg.Connection[TupleRow]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_MIGRATIONS_TABLE_DDL)
        cur.execute("SELECT version FROM schema_migrations")
        return {str(row[0]) for row in cur.fetchall()}


def apply_migrations(conn: psycopg.Connection[TupleRow]) -> list[str]:
    """Apply every not-yet-applied migration in order. Each migration runs
    in its own transaction and is recorded on success. Returns the versions
    newly applied (empty if the schema was already current)."""
    with conn.cursor() as cur:
        cur.execute(_MIGRATIONS_TABLE_DDL)
    conn.commit()
    already = applied_versions(conn)
    conn.commit()

    newly_applied: list[str] = []
    for version, sql in load_migrations():
        if version in already:
            continue
        with conn.cursor() as cur:
            for statement in split_statements(sql):
                cur.execute(statement)  # type: ignore[arg-type]
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                (version,),
            )
        conn.commit()
        newly_applied.append(version)
    return newly_applied
