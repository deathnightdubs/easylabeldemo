"""Opening libraries written by older versions.

These exist because a schema change once shipped without a migration, and the result was that
an existing collection could not be opened at all. The last test in this file is the guard
against that happening again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select, text

from numis import SCHEMA_VERSION
from numis.db import create_library, missing_schema_items, open_library
from numis.errors import SchemaVersionError
from numis.migrations import MIGRATIONS, has_column, has_table, pending
from numis.models import Base, Specimen
from numis.services import CollectionService


def _library_with_coins(path: Path, names: tuple[str, ...] = ("Maria Theresia", "Victoria")):
    library = create_library(path)
    with library.session() as session:
        service = CollectionService(session)
        subcollection = service.create_subcollection("Modern", naming_template="{ruler}")
        field = service.create_field("ruler", "Ruler", "text")
        service.show_field(subcollection, field, show_in_table=True)
        for name in names:
            service.add_specimen(subcollection, values={"ruler": name})
    library.close()
    return path


def _make_it_look_older(path: Path, version: str = "0001") -> None:
    """Turn a current library into one from before 0002, as a real old file would be."""
    connection = sqlite3.connect(path / "collection.db")
    try:
        connection.execute("ALTER TABLE specimen DROP COLUMN display_name_manual")
        connection.execute("UPDATE library_meta SET schema_version = ?", (version,))
        connection.commit()
    finally:
        connection.close()


class TestMigrationCatalogue:
    def test_versions_are_ordered_and_unique(self):
        versions = [migration.version for migration in MIGRATIONS]
        assert versions == sorted(versions)
        assert len(versions) == len(set(versions))

    def test_the_newest_migration_matches_the_declared_schema_version(self):
        """Otherwise a library would migrate and still be marked out of date."""
        assert MIGRATIONS[-1].version == SCHEMA_VERSION

    def test_pending_is_empty_for_a_current_library(self):
        assert pending(SCHEMA_VERSION) == []

    def test_pending_lists_what_an_old_library_needs(self):
        assert [m.version for m in pending("0001")] == ["0002"]

    def test_every_migration_explains_itself(self):
        for migration in MIGRATIONS:
            assert migration.description
            assert migration.description[0].islower() or migration.description[0].isupper()


class TestUpgradingAnOldLibrary:
    def test_it_opens_and_keeps_every_coin(self, tmp_path: Path):
        """The reported failure: 'no such column: specimen.display_name_manual'."""
        path = _library_with_coins(tmp_path / "Old.numis")
        _make_it_look_older(path)

        library = open_library(path)
        try:
            with library.session() as session:
                service = CollectionService(session)
                coins = list(session.scalars(service.live_specimens()))
                assert [coin.display_name for coin in coins] == ["Maria Theresia", "Victoria"]
                assert service.display(coins[0], "ruler") == "Maria Theresia"
        finally:
            library.close()

    def test_the_version_is_brought_up_to_date(self, tmp_path: Path):
        path = _library_with_coins(tmp_path / "Old.numis")
        _make_it_look_older(path)

        library = open_library(path)
        try:
            with library.session() as session:
                assert library.meta(session).schema_version == SCHEMA_VERSION
        finally:
            library.close()

    def test_a_backup_is_taken_first(self, tmp_path: Path):
        """Migrating somebody's collection with no copy to fall back on is unacceptable."""
        path = _library_with_coins(tmp_path / "Old.numis")
        _make_it_look_older(path)
        assert not list((path / "backups").glob("*.db"))

        library = open_library(path)
        try:
            backups = list((path / "backups").glob("*.db"))
            assert len(backups) == 1
            assert f"pre-{SCHEMA_VERSION}" in backups[0].name

            # The copy really is the old schema, so it is worth something.
            connection = sqlite3.connect(backups[0])
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_xinfo(specimen)")
                }
                assert "display_name_manual" not in columns
            finally:
                connection.close()
        finally:
            library.close()

    def test_existing_names_are_treated_as_typed_by_hand(self, tmp_path: Path):
        """Assuming they were generated would let a later edit overwrite someone's writing."""
        path = _library_with_coins(tmp_path / "Old.numis")
        _make_it_look_older(path)

        library = open_library(path)
        try:
            with library.session() as session:
                coins = list(session.scalars(CollectionService(session).live_specimens()))
                assert all(coin.display_name_manual for coin in coins)
        finally:
            library.close()

    def test_a_coin_with_no_name_is_left_automatic(self, tmp_path: Path):
        path = tmp_path / "Old.numis"
        library = create_library(path)
        with library.session() as session:
            service = CollectionService(session)
            subcollection = service.create_subcollection("Plain")
            service.add_specimen(subcollection)
        library.close()
        _make_it_look_older(path)

        library = open_library(path)
        try:
            with library.session() as session:
                coin = session.scalars(select(Specimen)).first()
                assert coin.display_name == ""
                assert not coin.display_name_manual
        finally:
            library.close()

    def test_opening_twice_is_harmless(self, tmp_path: Path):
        path = _library_with_coins(tmp_path / "Old.numis")
        _make_it_look_older(path)
        for _ in range(2):
            library = open_library(path)
            library.close()
        assert len(list((path / "backups").glob("*.db"))) == 1  # only the first needed one

    def test_a_current_library_is_not_migrated_or_backed_up(self, tmp_path: Path):
        path = _library_with_coins(tmp_path / "Current.numis")
        library = open_library(path)
        try:
            assert not list((path / "backups").glob("*.db"))
        finally:
            library.close()


class TestIdempotence:
    def test_a_migration_can_be_run_against_a_library_that_already_has_the_change(
        self, tmp_path: Path
    ):
        """So a library created by a newer build, but labelled older, still opens."""
        path = _library_with_coins(tmp_path / "Mixed.numis")
        connection = sqlite3.connect(path / "collection.db")
        try:
            connection.execute("UPDATE library_meta SET schema_version = '0001'")
            connection.commit()
        finally:
            connection.close()

        library = open_library(path)  # must not fail trying to add an existing column
        try:
            with library.session() as session:
                assert library.meta(session).schema_version == SCHEMA_VERSION
        finally:
            library.close()

    def test_the_helpers_report_what_is_there(self, tmp_path: Path):
        library = create_library(tmp_path / "Probe.numis")
        try:
            with library.engine.begin() as connection:
                assert has_table(connection, "specimen")
                assert not has_table(connection, "nonsense")
                assert has_column(connection, "specimen", "display_name_manual")
                assert not has_column(connection, "specimen", "nonsense")
                # Generated columns are hidden from table_info but must still be seen.
                assert has_column(connection, "specimen_event", "net_minor")
        finally:
            library.close()


class TestGuardAgainstTheSameMistake:
    def test_a_newer_library_is_refused_with_advice(self, tmp_path: Path):
        path = _library_with_coins(tmp_path / "Future.numis")
        connection = sqlite3.connect(path / "collection.db")
        try:
            connection.execute("UPDATE library_meta SET schema_version = '9999'")
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(SchemaVersionError) as info:
            open_library(path)
        assert "newer version" in str(info.value)
        assert "Update the application" in str(info.value)

    def test_a_missing_column_is_reported_readably_not_as_a_stack_trace(self, tmp_path: Path):
        """If a change ever ships without a migration again, this is what the user sees."""
        path = _library_with_coins(tmp_path / "Broken.numis")
        connection = sqlite3.connect(path / "collection.db")
        try:
            # A column the models expect, with the version left current so no migration runs.
            connection.execute("ALTER TABLE specimen DROP COLUMN is_favourite")
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(SchemaVersionError) as info:
            open_library(path)
        message = str(info.value)
        assert "specimen.is_favourite" in message
        assert "without a migration" in message

    def test_a_current_library_reports_nothing_missing(self, tmp_path: Path):
        library = create_library(tmp_path / "Fresh.numis")
        try:
            assert missing_schema_items(library) == []
        finally:
            library.close()

    def test_the_models_and_a_fresh_library_agree(self, tmp_path: Path):
        """Every table the models declare is actually created."""
        library = create_library(tmp_path / "Fresh.numis")
        try:
            with library.engine.connect() as connection:
                present = {
                    row[0]
                    for row in connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
            for table in Base.metadata.sorted_tables:
                assert table.name in present
        finally:
            library.close()
