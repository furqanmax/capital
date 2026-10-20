"""The SQL reference schema and the ORM must never drift apart.

``database/schema.sql`` is the human-readable contract; ``models/__init__.py`` is
what actually runs.  This test parses the SQL and compares it with the ORM
metadata table by table and column by column.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import inspect as sa_inspect

from database.db import PROJECT_ROOT

SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"

_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>\w+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_INDEX_RE = re.compile(
    r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>\w+)\s+ON\s+(?P<table>\w+)\s*\((?P<cols>[^)]*)\)",
    re.IGNORECASE,
)


def parse_schema(text: str) -> dict[str, set[str]]:
    """``{table: {column, ...}}`` from the reference DDL."""
    tables: dict[str, set[str]] = {}
    for match in _CREATE_RE.finditer(text):
        name = match.group("name")
        columns: set[str] = set()
        for line in match.group("body").splitlines():
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY",
                                                    "UNIQUE", "CHECK", "CONSTRAINT")):
                continue
            token = line.split()[0].strip('"`[]')
            if token:
                columns.add(token)
        tables[name] = columns
    return tables


@pytest.fixture(scope="module")
def reference() -> dict[str, set[str]]:
    assert SCHEMA_FILE.exists(), f"missing {SCHEMA_FILE}"
    return parse_schema(SCHEMA_FILE.read_text(encoding="utf-8"))


def test_schema_file_exists() -> None:
    assert SCHEMA_FILE.exists()
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    assert "PRAGMA foreign_keys = ON" in text


def test_every_reference_table_exists_in_orm(reference, db) -> None:
    orm_tables = set(db.table_names())
    missing = sorted(set(reference) - orm_tables)
    assert not missing, f"tables in schema.sql but not created by the ORM: {missing}"


def test_every_orm_table_is_documented(reference, db) -> None:
    undocumented = sorted(set(db.table_names()) - set(reference) - {"sqlite_sequence"})
    assert not undocumented, f"ORM tables missing from schema.sql: {undocumented}"


def test_columns_match_for_every_table(reference, db) -> None:
    inspector = sa_inspect(db.engine)
    problems: list[str] = []
    for table, expected in sorted(reference.items()):
        if not inspector.has_table(table):
            problems.append(f"{table}: table missing")
            continue
        actual = {column["name"] for column in inspector.get_columns(table)}
        if expected - actual:
            problems.append(f"{table}: in SQL but not in ORM -> {sorted(expected - actual)}")
        if actual - expected:
            problems.append(f"{table}: in ORM but not in SQL -> {sorted(actual - expected)}")
    assert not problems, "\n".join(problems)


def test_primary_keys_are_declared(reference, db) -> None:
    inspector = sa_inspect(db.engine)
    for table in reference:
        pk = inspector.get_pk_constraint(table)
        assert pk["constrained_columns"], f"{table} has no primary key"


def test_reference_indexes_exist(db) -> None:
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    inspector = sa_inspect(db.engine)

    expected: set[tuple[str, frozenset]] = set()
    for match in _INDEX_RE.finditer(text):
        expected.add((match.group("table"),
                      frozenset(c.strip() for c in match.group("cols").split(","))))

    actual: set[tuple[str, frozenset]] = set()
    for table in {table for table, _ in expected}:
        for index in inspector.get_indexes(table):
            actual.add((table, frozenset(index["column_names"])))

    missing = sorted(f"{table}{sorted(cols)}" for table, cols in expected - actual)
    assert not missing, f"indexes declared in schema.sql but absent from the database: {missing}"


def test_foreign_keys_are_enforced(db) -> None:
    """The importer relies on ON DELETE CASCADE, which needs PRAGMA foreign_keys."""
    with db.engine.connect() as conn:
        value = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
    assert value in (1, True)

    inspector = sa_inspect(db.engine)
    cascades = {
        fk["referred_table"]
        for fk in inspector.get_foreign_keys("prices")
    }
    assert "stocks" in cascades
