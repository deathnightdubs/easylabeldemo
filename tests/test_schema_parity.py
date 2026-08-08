"""The ORM models and the normative SQL must define the same schema.

``docs/design/schema/base-v1.sql`` is the specification; the models are the implementation.
If they drift, one of them is lying to whoever reads it, so this test fails loudly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from numis.db import memory_library
from numis.models import FTS_DDL

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "docs" / "design" / "schema" / "base-v1.sql"


def _describe(connection: sqlite3.Connection) -> dict[str, object]:
    """Tables, columns, indexes and triggers, in a comparable form."""
    tables = sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'specimen_fts%'"
        )
    )
    columns = {
        table: sorted(row[1] for row in connection.execute(f"PRAGMA table_xinfo({table})"))
        for table in tables
    }
    indexes: dict[str, tuple] = {}
    for table in tables:
        for row in connection.execute(f"PRAGMA index_list({table})"):
            name, unique, origin, partial = row[1], row[2], row[3], row[4]
            if name.startswith("sqlite_autoindex"):
                continue
            cols = tuple(
                info[2] if info[2] is not None else "<expr>"
                for info in connection.execute(f"PRAGMA index_info({name})")
            )
            indexes[name] = (table, cols, bool(unique), bool(partial), origin)
    triggers = sorted(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    )
    return {"tables": tables, "columns": columns, "indexes": indexes, "triggers": triggers}


@pytest.fixture(scope="module")
def reference() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_SQL.read_text())
    try:
        return _describe(connection)
    finally:
        connection.close()


@pytest.fixture(scope="module")
def implemented(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    library = memory_library()
    raw = library.engine.raw_connection()
    try:
        return _describe(raw.driver_connection)
    finally:
        raw.close()
        library.close()


def test_same_tables(reference, implemented):
    assert implemented["tables"] == reference["tables"]


def test_same_columns(reference, implemented):
    differences = {
        table: (
            sorted(set(implemented["columns"][table]) - set(reference["columns"][table])),
            sorted(set(reference["columns"][table]) - set(implemented["columns"][table])),
        )
        for table in reference["columns"]
        if implemented["columns"][table] != reference["columns"][table]
    }
    assert not differences, f"column differences (only-ORM, only-SQL): {differences}"


def test_same_index_names(reference, implemented):
    assert set(implemented["indexes"]) == set(reference["indexes"])


def test_indexes_agree_on_table_columns_and_uniqueness(reference, implemented):
    mismatches = {}
    for name, expected in reference["indexes"].items():
        actual = implemented["indexes"].get(name)
        if actual is None:
            continue
        # Compare table, columns, uniqueness and whether the index is partial. The exact
        # WHERE text is not compared: identical predicates can be spelled differently.
        if actual[:4] != expected[:4]:
            mismatches[name] = {"sql": expected[:4], "orm": actual[:4]}
    assert not mismatches, f"index definitions differ: {mismatches}"


def test_fts_triggers_present(reference, implemented):
    assert implemented["triggers"] == reference["triggers"]
    assert "specimen_search_ai" in implemented["triggers"]


def test_fts_table_created_by_models():
    library = memory_library()
    try:
        with library.engine.connect() as connection:
            found = connection.execute(
                text("SELECT name FROM sqlite_master WHERE name='specimen_fts'")
            ).fetchone()
        assert found is not None
        assert len(FTS_DDL) == 4  # the table plus three synchronising triggers
    finally:
        library.close()
