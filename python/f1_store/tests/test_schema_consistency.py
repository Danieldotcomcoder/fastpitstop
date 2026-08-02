"""The Python schema mirror (`schema.py`) and the SQL migration must agree —
they are two encodings of one truth, and drift between them is a silent
data-loss bug (a COPY column list that no longer matches the table)."""

from __future__ import annotations

import re

from f1_store.migrations import load_migrations
from f1_store.schema import (
    DRIVERS_COLUMNS,
    IDENTITY_KEYS,
    SESSIONS_COLUMNS,
    TABLE_COLUMNS,
)


def _migration_sql() -> str:
    return "\n".join(sql for _, sql in load_migrations())


def _create_table_body(sql: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.DOTALL | re.IGNORECASE
    )
    assert match is not None, f"no CREATE TABLE for {table}"
    return match.group(1)


def test_every_table_has_a_create_statement() -> None:
    sql = _migration_sql()
    tables = set(TABLE_COLUMNS) | {"sessions", "drivers"}
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql, table


def test_every_copy_column_exists_in_its_table() -> None:
    sql = _migration_sql()
    for table, columns in TABLE_COLUMNS.items():
        body = _create_table_body(sql, table)
        declared = set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s+\w", body, re.MULTILINE))
        for column in columns:
            assert column in declared, f"{table}.{column} missing from migration"


def test_materialized_state_columns_exist() -> None:
    sql = _migration_sql()
    for table, columns in (("sessions", SESSIONS_COLUMNS), ("drivers", DRIVERS_COLUMNS)):
        body = _create_table_body(sql, table)
        declared = set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s+\w", body, re.MULTILINE))
        for column in columns:
            assert column in declared, f"{table}.{column} missing from migration"


def test_identity_keys_cover_every_table() -> None:
    assert set(IDENTITY_KEYS) == set(TABLE_COLUMNS)
    for table, key in IDENTITY_KEYS.items():
        assert set(key).issubset(TABLE_COLUMNS[table]), table
