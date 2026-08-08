"""Undoable operations.

Every change the user makes goes through one of these, so undo and redo cover everything
rather than a chosen subset.

One Qt detail shapes the design: pushing a command onto a ``QUndoStack`` calls its ``redo()``
immediately. So these commands must not have already applied the change before being pushed,
or it would be applied twice. Nothing here touches the database until ``redo()`` runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtGui import QUndoCommand

from ..fields import parse_value
from ..models import FieldDefinition, Specimen, Subcollection
from ..services import CollectionService


class _Command(QUndoCommand):
    def __init__(self, service: CollectionService, text: str) -> None:
        super().__init__(text)
        self.service = service

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

    def redo(self) -> None:
        first_time = not self._previous
        for index, (specimen_id, field_id, raw) in enumerate(self.edits):
            specimen, field = self._specimen(specimen_id), self._field(field_id)
            if first_time:
                self._previous.append(self.service.raw_columns(specimen, field))
            if raw == "":
                self.service.write_columns(specimen, field, None)
            else:
                self.service.set_value(specimen, field, raw)
            del index
        self.session.commit()

    def undo(self) -> None:
        for (specimen_id, field_id, _), previous in zip(self.edits, self._previous, strict=True):
            specimen, field = self._specimen(specimen_id), self._field(field_id)
            self.service.write_columns(specimen, field, previous)
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

    def redo(self) -> None:
        specimen = self._specimen(self.specimen_id)
        self._previous = specimen.display_name
        specimen.display_name = self.name
        self.session.commit()

    def undo(self) -> None:
        self._specimen(self.specimen_id).display_name = self._previous
        self.session.commit()


class SetInventoryCode(_Command):
    """Change a coin's identifier."""

    def __init__(self, service: CollectionService, specimen_id: int, code: str | None) -> None:
        super().__init__(service, "change ID")
        self.specimen_id = specimen_id
        self.code = code
        self._previous: str | None = None

    def redo(self) -> None:
        specimen = self._specimen(self.specimen_id)
        self._previous = specimen.inventory_code
        self.service.set_inventory_code(specimen, self.code)
        self.session.commit()

    def undo(self) -> None:
        self.service.set_inventory_code(self._specimen(self.specimen_id), self._previous)
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
