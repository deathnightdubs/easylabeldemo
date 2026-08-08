"""Creating and opening libraries."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from numis import SCHEMA_VERSION
from numis.db import (
    BACKUPS_DIRNAME,
    DB_FILENAME,
    EXPORTS_DIRNAME,
    PRESETS_DIRNAME,
    create_library,
    open_library,
)
from numis.errors import LibraryError, SchemaVersionError
from numis.models import (
    Catalog,
    FieldDefinition,
    GradeModifier,
    GradeScale,
    GradingCompany,
    Specimen,
    Subcollection,
)
from numis.services import CollectionService


def test_a_library_is_a_folder(tmp_path: Path):
    """So that backing up and moving a collection are single operations."""
    library = create_library(tmp_path / "Test.numis")
    try:
        assert (library.path / DB_FILENAME).is_file()
        for name in (BACKUPS_DIRNAME, PRESETS_DIRNAME, EXPORTS_DIRNAME):
            assert (library.path / name).is_dir()
    finally:
        library.close()


def test_a_new_library_is_completely_empty(tmp_path: Path):
    """No catalogues, no grading scales, no example fields: a genuine blank slate."""
    library = create_library(tmp_path / "Test.numis")
    try:
        with library.session() as session:
            for model in (
                Subcollection, FieldDefinition, Specimen, Catalog,
                GradeScale, GradeModifier, GradingCompany,
            ):
                assert session.query(model).count() == 0, model.__name__
    finally:
        library.close()


def test_library_metadata_is_recorded(tmp_path: Path):
    library = create_library(tmp_path / "Test.numis", currency_symbol="£", currency_code="GBP")
    try:
        with library.session() as session:
            meta = library.meta(session)
            assert meta.schema_version == SCHEMA_VERSION
            assert meta.currency_symbol == "£"
            assert meta.currency_code == "GBP"
            assert meta.library_uuid
    finally:
        library.close()


def test_creating_over_an_existing_library_is_refused(tmp_path: Path):
    path = tmp_path / "Test.numis"
    library = create_library(path)
    library.close()
    with pytest.raises(LibraryError):
        create_library(path)


def test_opening_a_missing_library_says_so(tmp_path: Path):
    with pytest.raises(LibraryError) as info:
        open_library(tmp_path / "nothing.numis")
    assert "no library" in str(info.value)


def test_a_library_survives_being_closed_and_reopened(tmp_path: Path):
    path = tmp_path / "Test.numis"
    library = create_library(path)
    with library.session() as session:
        svc = CollectionService(session)
        sub = svc.create_subcollection("Modern")
        svc.create_field("ruler", "Ruler", "text")
        svc.add_specimen(sub, values={"ruler": "Victoria"}, display_name="Crown")
    library.close()

    reopened = open_library(path)
    try:
        with reopened.session() as session:
            svc = CollectionService(session)
            coin = session.query(Specimen).one()
            assert coin.display_name == "Crown"
            assert svc.display(coin, "ruler") == "Victoria"
    finally:
        reopened.close()


def test_a_newer_library_is_refused_rather_than_guessed_at(tmp_path: Path):
    path = tmp_path / "Test.numis"
    library = create_library(path)
    with library.session() as session:
        library.meta(session).schema_version = "9999"
    library.close()

    with pytest.raises(SchemaVersionError) as info:
        open_library(path)
    assert "newer version" in str(info.value)


def test_an_older_library_reports_that_migration_is_not_implemented(tmp_path: Path):
    path = tmp_path / "Test.numis"
    library = create_library(path)
    with library.session() as session:
        library.meta(session).schema_version = "0000"
    library.close()

    with pytest.raises(SchemaVersionError) as info:
        open_library(path)
    assert "Migration is not implemented" in str(info.value)


def test_foreign_keys_are_enforced_on_every_connection(tmp_path: Path):
    """Off by default in SQLite and per-connection, so this is easy to get wrong."""
    library = create_library(tmp_path / "Test.numis")
    try:
        with library.engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    finally:
        library.close()


def test_backup_produces_a_usable_copy(tmp_path: Path):
    library = create_library(tmp_path / "Test.numis")
    try:
        with library.session() as session:
            CollectionService(session).create_subcollection("Modern")
        backup = library.backup("test")
        assert backup.is_file()
        assert backup.parent.name == BACKUPS_DIRNAME

        import sqlite3

        connection = sqlite3.connect(backup)
        try:
            assert connection.execute("SELECT count(*) FROM subcollection").fetchone()[0] == 1
        finally:
            connection.close()
    finally:
        library.close()


def test_naive_datetimes_are_refused(tmp_path: Path):
    """Guessing a timezone is quiet corruption that cannot be detected later."""
    from datetime import datetime

    from sqlalchemy.exc import StatementError

    library = create_library(tmp_path / "Test.numis")
    try:
        # SQLAlchemy wraps the ValueError raised by the column type, so the refusal arrives
        # as a StatementError with the original message inside it.
        with (
            pytest.raises(StatementError, match="naive datetime"),
            library.session() as session,
        ):
            svc = CollectionService(session)
            sub = svc.create_subcollection("Modern")
            coin = svc.add_specimen(sub)
            coin.deleted_at = datetime(2026, 1, 1)  # no timezone
            session.flush()
    finally:
        library.close()
