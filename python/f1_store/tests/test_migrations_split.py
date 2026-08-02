"""The SQL statement splitter — the one bit of the migration runner that
must be correct without a database to lean on."""

from __future__ import annotations

from f1_store.migrations import load_migrations, split_statements


def test_splits_on_top_level_semicolons() -> None:
    sql = "CREATE TABLE a (x int); CREATE TABLE b (y int);"
    assert split_statements(sql) == ["CREATE TABLE a (x int)", "CREATE TABLE b (y int)"]


def test_ignores_semicolons_inside_string_literals() -> None:
    sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
    assert split_statements(sql) == ["INSERT INTO t VALUES ('a;b')", "SELECT 1"]


def test_handles_doubled_quote_escapes() -> None:
    sql = "SELECT 'it''s; fine'; SELECT 2;"
    assert split_statements(sql) == ["SELECT 'it''s; fine'", "SELECT 2"]


def test_strips_line_comments_and_blank_fragments() -> None:
    sql = """
    -- a leading comment; with a semicolon
    SELECT 1;
    -- trailing comment
    """
    assert split_statements(sql) == ["SELECT 1"]


def test_ignores_semicolons_inside_dollar_quotes() -> None:
    sql = (
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN; RETURN 1; END; $$ LANGUAGE plpgsql; SELECT 1;"
    )
    statements = split_statements(sql)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE FUNCTION")
    assert "$$ BEGIN; RETURN 1; END; $$" in statements[0]
    assert statements[1] == "SELECT 1"


def test_final_statement_without_trailing_semicolon_is_kept() -> None:
    assert split_statements("SELECT 1") == ["SELECT 1"]


def test_real_migration_splits_into_statements() -> None:
    (_, sql), *_ = load_migrations()
    statements = split_statements(sql)
    # Sanity: the initial migration is many statements, and the extension
    # and the events hypertable are both present as their own statements.
    assert len(statements) > 15
    assert any(s.startswith("CREATE EXTENSION") for s in statements)
    assert any("create_hypertable('events'" in s for s in statements)
