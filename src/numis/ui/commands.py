"""Undoable operations.

Every change the user makes goes through one of these, so undo and redo cover everything
rather than a chosen subset.

One Qt detail shapes the design: pushing a command onto a ``QUndoStack`` calls its ``redo()``
immediately. So these commands must not have already applied the change before being pushed,
or it would be applied twice. Nothing here touches the database until ``redo()`` runs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtGui import QUndoCommand

from ..fields import parse_value
from ..models import FieldDefinition, Specimen, Subcollection
from ..services import CollectionService


def readable(error: Exception) -> str:
    """Turn a database error into something worth reading."""
    text = str(getattr(error, "orig", error))
    if "UNIQUE constraint failed" in text:
        return "That is already recorded on this coin."
    return text.splitlines()[0]


class _Command(QUndoCommand):
    def __init__(self, service: CollectionService, text: str) -> None:
        super().__init__(text)
        self.service = service
        #: Set when the work failed. Pushing a command onto the stack calls it through Qt's
        #: C++ layer, which a Python exception cannot cross — it would be printed and lost,
        #: leaving the session broken and every later operation failing. So each command
        #: catches its own failure, rolls back, and leaves the reason here for the caller.
        self.error: str | None = None

    def guarded(self, work: Callable[[], object]) -> bool:
        try:
            work()
            return True
        except Exception as exc:  # noqa: BLE001 - the point is to catch everything
            self.session.rollback()
            self.error = readable(exc)
            return False

    @property
    def session(self):  # noqa: ANN201
        return self.service.session

    def _specimen(self, specimen_id: int) -> Specimen:
        return self.session.get(Specimen, specimen_id)

    def _field(self, field_id: int) -> FieldDefinition:
        return self.session.get(FieldDefinition, field_id)


class SetValues(_Command):
    """Set one or more cells, remembering exactly what was there before.

    Restoring the previous *stored columns* rather than re-parsing the previous display text
    matters: re-parsing would silently discard a sort key the user had set by hand.
    """

    def __init__(
        self,
        service: CollectionService,
        edits: Sequence[tuple[int, int, str]],
        *,
        text: str | None = None,
    ) -> None:
        count = len(edits)
        super().__init__(service, text or (f"edit {count} cells" if count > 1 else "edit cell"))
        self.edits = list(edits)
        self._previous: list[dict[str, Any] | None] = []
        self._previous_names: dict[int, tuple[str, int]] = {}

    def redo(self) -> None:
        self.guarded(self._redo)

    def _redo(self) -> None:
        first_time = not self._previous
        touched: set[int] = set()
        for specimen_id, field_id, raw in self.edits:
            specimen, field = self._specimen(specimen_id), self._field(field_id)
            if first_time:
                self._previous.append(self.service.raw_columns(specimen, field))
                self._previous_names.setdefault(
                    specimen_id, (specimen.display_name, int(specimen.display_name_manual))
                )
            if raw == "":
                self.service.write_columns(specimen, field, None)
            else:
                self.service.set_value(specimen, field, raw)
            touched.add(specimen_id)

        # A coin with no name yet takes one from its subcollection's template, so filling in
        # values after creating the row produces a name. A name typed by hand is never touched.
        for specimen_id in touched:
            self.service.refresh_display_name(self._specimen(specimen_id))
        self.session.commit()

    def undo(self) -> None:
        for (specimen_id, field_id, _), previous in zip(self.edits, self._previous, strict=True):
            specimen, field = self._specimen(specimen_id), self._field(field_id)
            self.service.write_columns(specimen, field, previous)
        for specimen_id, (name, manual) in self._previous_names.items():
            specimen = self._specimen(specimen_id)
            specimen.display_name, specimen.display_name_manual = name, manual
        self.session.commit()


class SetSortValue(_Command):
    """Record a sort position the user chose for one cell."""

    def __init__(
        self, service: CollectionService, specimen_id: int, field_id: int, sort_value: float
    ) -> None:
        super().__init__(service, "set sort value")
        self.specimen_id = specimen_id
        self.field_id = field_id
        self.sort_value = sort_value
        self._previous: dict[str, Any] | None = None

    def redo(self) -> None:
        specimen, field = self._specimen(self.specimen_id), self._field(self.field_id)
        self._previous = self.service.raw_columns(specimen, field)
        self.service.set_sort_value(specimen, field, self.sort_value)
        self.session.commit()

    def undo(self) -> None:
        specimen, field = self._specimen(self.specimen_id), self._field(self.field_id)
        self.service.write_columns(specimen, field, self._previous)
        self.session.commit()


class SetDisplayName(_Command):
    """Rename a coin.

    The name is normally rendered from the subcollection's template, but a name typed by hand
    is kept as typed and not regenerated.
    """

    def __init__(self, service: CollectionService, specimen_id: int, name: str) -> None:
        super().__init__(service, "rename coin")
        self.specimen_id = specimen_id
        self.name = name
        self._previous = ""
        self._previous_manual = 0

    def redo(self) -> None:
        specimen = self._specimen(self.specimen_id)
        self._previous = specimen.display_name
        self._previous_manual = int(specimen.display_name_manual)
        self.service.set_display_name(specimen, self.name)
        self.session.commit()

    def undo(self) -> None:
        specimen = self._specimen(self.specimen_id)
        specimen.display_name = self._previous
        specimen.display_name_manual = self._previous_manual
        self.session.commit()


class SetInventoryCode(_Command):
    """Change a coin's identifier.

    Reusing the code of a coin in the Trash takes it away from that coin, so undo has to put
    both halves back.
    """

    def __init__(
        self,
        service: CollectionService,
        specimen_id: int,
        code: str | None,
        *,
        reuse_from_trash: bool = False,
    ) -> None:
        super().__init__(service, "change ID")
        self.specimen_id = specimen_id
        self.code = code
        self.reuse_from_trash = reuse_from_trash
        self._previous: str | None = None
        self._released_id: int | None = None
        self._released_code: str | None = None

    def redo(self) -> None:
        self.guarded(self._redo)

    def _redo(self) -> None:
        specimen = self._specimen(self.specimen_id)
        self._previous = specimen.inventory_code
        released = self.service.set_inventory_code(
            specimen, self.code, reuse_from_trash=self.reuse_from_trash
        )
        if released is not None:
            self._released_id, self._released_code = released.id, self.code
        self.session.commit()

    def undo(self) -> None:
        self.service.set_inventory_code(self._specimen(self.specimen_id), self._previous)
        if self._released_id is not None:
            self._specimen(self._released_id).inventory_code = self._released_code
        self.session.commit()


class SetStatus(_Command):
    """Change whether a coin is owned, sold, lost and so on."""

    def __init__(
        self, service: CollectionService, specimen_ids: Sequence[int], status: str
    ) -> None:
        count = len(specimen_ids)
        super().__init__(service, f"mark {count} coin{'s' if count > 1 else ''} as {status}")
        self.specimen_ids = list(specimen_ids)
        self.status = status
        self._previous: dict[int, str] = {}

    def redo(self) -> None:
        for specimen_id in self.specimen_ids:
            specimen = self._specimen(specimen_id)
            self._previous[specimen_id] = specimen.status
            self.service.set_status(specimen, self.status)
        self.session.commit()

    def undo(self) -> None:
        for specimen_id, status in self._previous.items():
            self.service.set_status(self._specimen(specimen_id), status)
        self.session.commit()


class MoveSpecimens(_Command):
    """Move coins into another subcollection, remembering where each came from."""

    def __init__(
        self, service: CollectionService, specimen_ids: Sequence[int], subcollection_id: int
    ) -> None:
        count = len(specimen_ids)
        super().__init__(service, f"move {count} coin{'s' if count > 1 else ''}")
        self.specimen_ids = list(specimen_ids)
        self.subcollection_id = subcollection_id
        self._previous: dict[int, int] = {}

    def redo(self) -> None:
        target = self.session.get(Subcollection, self.subcollection_id)
        specimens = [self._specimen(identifier) for identifier in self.specimen_ids]
        self._previous = {specimen.id: specimen.subcollection_id for specimen in specimens}
        self.service.move_specimens(specimens, target)
        self.session.commit()

    def undo(self) -> None:
        for specimen_id, subcollection_id in self._previous.items():
            self._specimen(specimen_id).subcollection_id = subcollection_id
        self.session.commit()


class AddSpecimens(_Command):
    """Add rows. Undoing removes them outright, since they never existed before."""

    def __init__(
        self,
        service: CollectionService,
        subcollection_id: int,
        count: int,
        *,
        values: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(service, f"add {count} row{'s' if count > 1 else ''}")
        self.subcollection_id = subcollection_id
        self.count = count
        self.values = values or {}
        self.specimen_ids: list[int] = []

    def redo(self) -> None:
        subcollection = self.session.get(Subcollection, self.subcollection_id)
        made = self.service.bulk_add(subcollection, self.count, values=self.values)
        self.specimen_ids = [specimen.id for specimen in made]
        self.session.commit()

    def undo(self) -> None:
        for specimen_id in self.specimen_ids:
            specimen = self._specimen(specimen_id)
            if specimen is not None:
                self.service.purge(specimen)
        self.specimen_ids = []
        self.session.commit()


class DeleteSpecimens(_Command):
    """Move rows to the Trash. Nothing is destroyed, so undo simply restores them."""

    def __init__(self, service: CollectionService, specimen_ids: Sequence[int]) -> None:
        count = len(specimen_ids)
        super().__init__(service, f"delete {count} row{'s' if count > 1 else ''}")
        self.specimen_ids = list(specimen_ids)

    def redo(self) -> None:
        for specimen_id in self.specimen_ids:
            self.service.soft_delete(self._specimen(specimen_id))
        self.session.commit()

    def undo(self) -> None:
        for specimen_id in self.specimen_ids:
            self.service.restore(self._specimen(specimen_id))
        self.session.commit()


#: Columns never copied when a deleted row is recreated by undo.
_SKIPPED_COLUMNS = frozenset({"created_at", "updated_at"})


def _snapshot(row: object) -> dict[str, Any]:
    """Every stored column of a row, so undo can recreate it exactly."""
    return {
        name: getattr(row, name)
        for name in row.__table__.columns.keys()  # noqa: SIM118
        if name not in _SKIPPED_COLUMNS
    }


class AddChildRow(_Command):
    """Add a catalogue reference, grade, certification or link.

    The row is built by a callable rather than passed in, because nothing may touch the
    database until ``redo()`` runs: pushing a command onto the stack calls it immediately, so a
    pre-built row would be inserted twice.
    """

    def __init__(
        self,
        service: CollectionService,
        description: str,
        build: Callable[[CollectionService], object],
    ) -> None:
        super().__init__(service, description)
        self._build = build
        self._model: type | None = None
        self._row_id: int | None = None

    def redo(self) -> None:
        def work() -> None:
            row = self._build(self.service)
            self._model = type(row)
            self._row_id = row.id
            self.session.commit()

        self.guarded(work)

    def undo(self) -> None:
        if self._model is None or self._row_id is None:  # pragma: no cover
            return
        row = self.session.get(self._model, self._row_id)
        if row is not None:
            self.session.delete(row)
        self.session.commit()


class DeleteChildRow(_Command):
    """Remove a catalogue reference, grade, certification or link, reversibly."""

    def __init__(
        self, service: CollectionService, description: str, model: type, row_id: int
    ) -> None:
        super().__init__(service, description)
        self.model = model
        self.row_id = row_id
        self._columns: dict[str, Any] = {}
        self._modifier_links: list[dict[str, Any]] = []

    def redo(self) -> None:
        self.guarded(self._redo)

    def _redo(self) -> None:
        row = self.session.get(self.model, self.row_id)
        if row is None:  # pragma: no cover
            return
        self._columns = _snapshot(row)
        # A grade's modifiers live in a join table, and each instance carries its own detail
        # and issuing certification, so the whole link has to be remembered.
        if hasattr(row, "modifier_links"):
            self._modifier_links = [
                {
                    "grade_modifier_id": link.grade_modifier_id,
                    "detail": link.detail,
                    "certification_id": link.certification_id,
                    "sort_order": link.sort_order,
                }
                for link in row.modifier_links
            ]
        self.session.delete(row)
        self.session.commit()

    def undo(self) -> None:
        row = self.model(**self._columns)
        self.session.add(row)
        self.session.flush()
        if self._modifier_links:
            from ..models import SpecimenGradeModifier

            for link in self._modifier_links:
                self.session.add(SpecimenGradeModifier(specimen_grade_id=row.id, **link))
            self.session.flush()
            self.service.refresh_grade_text(row)
        self.session.commit()


def validate(service: CollectionService, field: FieldDefinition, raw: str) -> str | None:
    """Return an error message if ``raw`` cannot be stored in ``field``, else ``None``.

    Used before a command is pushed, so a rejected edit never enters the undo history.
    """
    if raw == "":
        return None
    try:
        parse_value(field.data_type, raw, service.field_config(field))
    except Exception as exc:  # FieldParseError and anything a parser raises
        return str(exc)
    return None
