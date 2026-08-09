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


# ---------------------------------------------------------------------------
# 0003 — grades are typed rather than chosen, modifiers gained display forms,
#        and rank replaced is_primary everywhere
# ---------------------------------------------------------------------------


def _drop_index(connection: Connection, name: str) -> None:
    connection.execute(text(f"DROP INDEX IF EXISTS {name}"))


def _rank_instead_of_primary(connection: Connection, table: str, index: str) -> None:
    """Give a table a rank, seeded from whichever row was the primary one."""
    if not has_column(connection, table, "rank"):
        connection.execute(
            text(f"ALTER TABLE {table} ADD COLUMN rank INTEGER NOT NULL DEFAULT 1")
        )
    if has_column(connection, table, "is_primary"):
        # The primary row becomes rank 1; everything else queues behind it in id order,
        # which is the order it was entered.
        connection.execute(
            text(
                f"UPDATE {table} SET rank = CASE WHEN is_primary = 1 THEN 1 ELSE 2 END"
            )
        )
        _drop_index(connection, index)
        connection.execute(text(f"ALTER TABLE {table} DROP COLUMN is_primary"))


def _grades_are_typed(connection: Connection) -> None:
    # specimen_grade: a typed label, what it counts as, and whether to name the grader
    for column, definition in (
        ("grade_label", "TEXT NOT NULL DEFAULT ''"),
        ("base_value", "REAL NULL"),
        ("hide_assigned_by", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if not has_column(connection, "specimen_grade", column):
            connection.execute(
                text(f"ALTER TABLE specimen_grade ADD COLUMN {column} {definition}")
            )

    # Existing grades took their label and value from the level they pointed at.
    connection.execute(
        text(
            """
            UPDATE specimen_grade
               SET grade_label = COALESCE(
                       (SELECT label FROM grade_level WHERE grade_level.id = grade_level_id),
                       grade_label
                   )
             WHERE grade_label = ''
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE specimen_grade
               SET base_value = (
                       SELECT normalised FROM grade_level WHERE grade_level.id = grade_level_id
                   )
             WHERE base_value IS NULL AND grade_level_id IS NOT NULL
            """
        )
    )
    # A grade with no level to fall back on keeps its comparable value as the base, so nothing
    # moves in a sorted column.
    connection.execute(
        text("UPDATE specimen_grade SET base_value = normalised WHERE base_value IS NULL")
    )
    _rank_instead_of_primary(connection, "specimen_grade", "ux_grade_primary")

    # grade_modifier: rebuilt, because widening a CHECK constraint needs a new table
    if not has_column(connection, "grade_modifier", "abbreviation"):
        connection.execute(
            text(
                """
                CREATE TABLE grade_modifier_new (
                  id               INTEGER PRIMARY KEY,
                  uuid             TEXT NOT NULL UNIQUE,
                  code             TEXT NOT NULL UNIQUE,
                  label            TEXT NOT NULL,
                  abbreviation     TEXT NULL,
                  kind             TEXT NOT NULL CHECK (kind IN
                                     ('detail','sticker','qualifier','strike','colour','contrast')),
                  issuer           TEXT NULL,
                  attach_without_space INTEGER NOT NULL DEFAULT 0,
                  normalised_delta REAL NOT NULL DEFAULT 0,
                  colour           TEXT NULL,
                  notes            TEXT NULL,
                  created_at       TEXT NOT NULL,
                  updated_at       TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO grade_modifier_new
                    (id, uuid, code, label, kind, normalised_delta, colour, notes,
                     created_at, updated_at)
                SELECT id, uuid, code, label, kind, normalised_delta, colour, notes,
                       created_at, updated_at
                  FROM grade_modifier
                """
            )
        )
        connection.execute(text("DROP TABLE grade_modifier"))
        connection.execute(text("ALTER TABLE grade_modifier_new RENAME TO grade_modifier"))
        # A plus or a star reads without a space; nothing else does.
        connection.execute(
            text(
                "UPDATE grade_modifier SET attach_without_space = 1 "
                "WHERE label IN ('+', '*') OR code IN ('PLUS', 'STAR')"
            )
        )

    # specimen_grade_modifier: rebuilt to give each instance its own key, detail and issuer
    if not has_column(connection, "specimen_grade_modifier", "id"):
        connection.execute(
            text(
                """
                CREATE TABLE specimen_grade_modifier_new (
                  id                INTEGER PRIMARY KEY,
                  specimen_grade_id INTEGER NOT NULL
                                      REFERENCES specimen_grade(id) ON DELETE CASCADE,
                  grade_modifier_id INTEGER NOT NULL
                                      REFERENCES grade_modifier(id) ON DELETE RESTRICT,
                  detail            TEXT NULL,
                  certification_id  INTEGER NULL REFERENCES certification(id) ON DELETE SET NULL,
                  sort_order        INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO specimen_grade_modifier_new
                    (specimen_grade_id, grade_modifier_id)
                SELECT specimen_grade_id, grade_modifier_id FROM specimen_grade_modifier
                """
            )
        )
        connection.execute(text("DROP TABLE specimen_grade_modifier"))
        connection.execute(
            text(
                "ALTER TABLE specimen_grade_modifier_new RENAME TO specimen_grade_modifier"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_sgm ON "
                "specimen_grade_modifier(specimen_grade_id, grade_modifier_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sgm_mod ON "
                "specimen_grade_modifier(grade_modifier_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sgm_cert ON "
                "specimen_grade_modifier(certification_id)"
            )
        )
        # A grade's problem note becomes the detail of its Details modifier, which is where
        # that text now belongs.
        connection.execute(
            text(
                """
                UPDATE specimen_grade_modifier
                   SET detail = (
                           SELECT detail_note FROM specimen_grade
                            WHERE specimen_grade.id = specimen_grade_modifier.specimen_grade_id
                       )
                 WHERE grade_modifier_id IN (SELECT id FROM grade_modifier WHERE kind = 'detail')
                """
            )
        )

    # the remaining two, plus links which only needed a rank
    _rank_instead_of_primary(connection, "catalog_reference", "ux_catref_prim")
    _rank_instead_of_primary(connection, "certification", "ux_cert_primary")
    if not has_column(connection, "external_link", "rank"):
        connection.execute(
            text("ALTER TABLE external_link ADD COLUMN rank INTEGER NOT NULL DEFAULT 1")
        )

    # indexes the models now expect
    for statement in (
        "DROP INDEX IF EXISTS ix_grade_spec",
        "CREATE INDEX IF NOT EXISTS ix_grade_spec ON specimen_grade(specimen_id, rank)",
        "DROP INDEX IF EXISTS ix_catref_spec",
        "CREATE INDEX IF NOT EXISTS ix_catref_spec ON catalog_reference(specimen_id, rank)",
        "DROP INDEX IF EXISTS ix_cert_spec",
        "CREATE INDEX IF NOT EXISTS ix_cert_spec ON certification(specimen_id, status, rank)",
        "DROP INDEX IF EXISTS ix_link_spec",
        "CREATE INDEX IF NOT EXISTS ix_link_spec ON external_link(specimen_id, rank, sort_order)",
    ):
        connection.execute(text(statement))


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version="0002",
        description="record whether a coin's name was typed by hand",
        apply=_add_display_name_manual,
    ),
    Migration(
        version="0003",
        description="grades are typed rather than chosen from a list; rank replaces is_primary",
        apply=_grades_are_typed,
    ),
)


def pending(from_version: str) -> list[Migration]:
    """Migrations needed to bring ``from_version`` up to date, in order."""
    return [migration for migration in MIGRATIONS if migration.version > from_version]


def latest_version(base: str = "0001") -> str:
    """The version a freshly migrated library ends up at."""
    return MIGRATIONS[-1].version if MIGRATIONS else base
