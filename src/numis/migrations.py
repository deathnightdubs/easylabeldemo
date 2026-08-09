"""Bringing an existing library up to the current schema.

Every schema change needs a migration here and a bump of
:data:`numis.SCHEMA_VERSION`. There is no exception to that, including during
development: the moment anyone has a library with real coins in it, changing the
models without a migration means their collection will not open.

That is not hypothetical — it is exactly what happened when ``display_name_manual``
was added, and the failure surfaced as a stack trace from inside the table view
rather than anything a person could act on.

Migrations are deliberately:

* **ordered** — applied in sequence from whatever version the library holds
* **idempotent** — each checks the state it is about to change, so re-running one,
  or running it against a library that already happens to have the change, is
  harmless
* **preceded by a backup** — taken by :func:`numis.db.open_library` before any of
  this runs
* **plain SQL** — no ORM. The models describe the *current* schema, so using them
  to migrate an older one is circular and breaks the moment they move again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class Migration:
    """One step from the previous schema version to :attr:`version`."""

    version: str
    description: str
    apply: Callable[[Connection], None]


def has_table(connection: Connection, table: str) -> bool:
    found = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"), {"name": table}
    ).fetchone()
    return found is not None


def has_column(connection: Connection, table: str, column: str) -> bool:
    """Whether a column exists, including generated ones.

    ``table_xinfo`` rather than ``table_info``: the latter hides generated columns,
    which would make a migration think ``net_minor`` was missing and try to add it.
    """
    if not has_table(connection, table):
        return False
    rows = connection.execute(text(f"PRAGMA table_xinfo({table})")).fetchall()
    return any(row[1] == column for row in rows)


# ---------------------------------------------------------------------------
# 0002 — remember whether a coin's name was typed by hand
# ---------------------------------------------------------------------------


def _add_display_name_manual(connection: Connection) -> None:
    if has_column(connection, "specimen", "display_name_manual"):
        return
    connection.execute(
        text(
            "ALTER TABLE specimen "
            "ADD COLUMN display_name_manual INTEGER NOT NULL DEFAULT 0"
        )
    )
    # Existing names are treated as typed by hand. The alternative — assuming they were
    # generated — would let a later edit overwrite a name somebody chose, and losing
    # someone's writing is worse than a name that has stopped following its template.
    # Clearing a name returns it to the template, so this is recoverable either way.
    connection.execute(
        text("UPDATE specimen SET display_name_manual = 1 WHERE display_name <> ''")
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version="0002",
        description="record whether a coin's name was typed by hand",
        apply=_add_display_name_manual,
    ),
)


def pending(from_version: str) -> list[Migration]:
    """Migrations needed to bring ``from_version`` up to date, in order."""
    return [migration for migration in MIGRATIONS if migration.version > from_version]


def latest_version(base: str = "0001") -> str:
    """The version a freshly migrated library ends up at."""
    return MIGRATIONS[-1].version if MIGRATIONS else base
