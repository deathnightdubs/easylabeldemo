"""Creating, opening and connecting to a library.

A library is a *folder*, not a bare file, so that "back up my collection" and "move my
collection to another computer" are single operations. See docs/design/01, Part 1.1.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from . import SCHEMA_VERSION, __version__
from .errors import LibraryError, SchemaVersionError
from .models import FTS_DDL, Base, LibraryMeta
from .sqltypes import utcnow

DB_FILENAME = "collection.db"
BACKUPS_DIRNAME = "backups"
PRESETS_DIRNAME = "presets"
EXPORTS_DIRNAME = "exports"

#: Created later, with virtual albums. Named here so the layout is stable from the start.
MEDIA_DIRNAME = "media"


@event.listens_for(Engine, "connect")
def _apply_pragmas(dbapi_connection: object, connection_record: object) -> None:
    """Apply SQLite settings to every new connection.

    ``foreign_keys`` is OFF by default in SQLite and is per-connection, so forgetting it
    silently disables every FOREIGN KEY in the schema. That is why this is a global hook
    rather than something each caller remembers.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA temp_store = MEMORY")
    finally:
        cursor.close()


@dataclass
class Library:
    """An open collection library."""

    path: Path
    engine: Engine
    session_factory: sessionmaker[Session]

    @property
    def db_path(self) -> Path:
        return self.path / DB_FILENAME

    @property
    def backups_path(self) -> Path:
        return self.path / BACKUPS_DIRNAME

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session that commits on success and rolls back on failure."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def meta(self, session: Session) -> LibraryMeta:
        meta = session.scalar(select(LibraryMeta).where(LibraryMeta.id == 1))
        if meta is None:  # pragma: no cover - would mean a corrupt library
            raise LibraryError(f"{self.db_path} has no library_meta row")
        return meta

    def backup(self, label: str = "manual") -> Path:
        """Copy the database into ``backups/`` and return the new path.

        Called before any migration. Uses SQLite's own backup API so the copy is consistent
        even if something else holds the database open.
        """
        self.backups_path.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.backups_path / f"collection-{stamp}-{label}.db"

        raw = self.engine.raw_connection()
        try:
            import sqlite3

            destination = sqlite3.connect(target)
            try:
                raw.driver_connection.backup(destination)  # type: ignore[union-attr]
            finally:
                destination.close()
        finally:
            raw.close()
        return target

    def close(self) -> None:
        self.engine.dispose()


def _engine_for(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}", future=True)


def _library(path: Path) -> Library:
    engine = _engine_for(path / DB_FILENAME)
    return Library(path=path, engine=engine, session_factory=sessionmaker(bind=engine))


def create_library(
    path: str | Path,
    *,
    currency_symbol: str = "$",
    currency_code: str = "USD",
    currency_decimals: int = 2,
    exist_ok: bool = False,
) -> Library:
    """Create a new library folder and its database.

    Deliberately creates **no** content: no catalogues, no grading scales, no example
    fields. A new library is genuinely empty and the user builds what they use.
    """
    path = Path(path)
    if path.exists() and any(path.iterdir()) and not exist_ok:
        raise LibraryError(f"{path} already exists and is not empty")

    for sub in (BACKUPS_DIRNAME, PRESETS_DIRNAME, EXPORTS_DIRNAME):
        (path / sub).mkdir(parents=True, exist_ok=True)

    library = _library(path)
    Base.metadata.create_all(library.engine)
    with library.engine.begin() as connection:
        for statement in FTS_DDL:
            connection.execute(text(statement))

    with library.session() as session:
        session.add(
            LibraryMeta(
                id=1,
                schema_version=SCHEMA_VERSION,
                app_version_created=__version__,
                app_version_last_opened=__version__,
                currency_symbol=currency_symbol,
                currency_code=currency_code,
                currency_decimals=currency_decimals,
            )
        )
    return library


def open_library(path: str | Path) -> Library:
    """Open an existing library.

    Refuses a library written by a newer application rather than attempting to read it:
    guessing at a future schema is how data gets destroyed.
    """
    path = Path(path)
    db_path = path / DB_FILENAME
    if not db_path.is_file():
        raise LibraryError(f"no library at {path} (expected {DB_FILENAME})")

    library = _library(path)
    with library.session() as session:
        meta = library.meta(session)
        if meta.schema_version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{db_path} was written by a newer version "
                f"(library schema {meta.schema_version}, this build supports {SCHEMA_VERSION})"
            )
        if meta.schema_version < SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{db_path} uses schema {meta.schema_version}; this build expects "
                f"{SCHEMA_VERSION}. Migration is not implemented yet."
            )
        meta.app_version_last_opened = __version__
        meta.updated_at = utcnow()
    return library


def memory_library() -> Library:
    """An in-memory library for tests.

    Uses a shared-cache URI so every connection in the pool sees the same database.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in FTS_DDL:
            connection.execute(text(statement))

    library = Library(
        path=Path(":memory:"), engine=engine, session_factory=sessionmaker(bind=engine)
    )
    with library.session() as session:
        session.add(
            LibraryMeta(
                id=1,
                schema_version=SCHEMA_VERSION,
                app_version_created=__version__,
            )
        )
    return library


def copy_library(source: str | Path, target: str | Path) -> Path:
    """Copy an entire library folder. Used by tests and by "save a copy"."""
    source, target = Path(source), Path(target)
    shutil.copytree(source, target)
    return target
