"""The spreadsheet grid.

A ``QAbstractTableModel`` over the collection. Values are loaded a table at a time rather than
a cell at a time, and every edit becomes an undoable command rather than a direct write.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QUndoStack

from ..models import FieldDefinition, Specimen, Subcollection
from ..services import CollectionService, Column
from .commands import SetValues, validate

#: Columns the model always shows, before the user's own fields.
FIXED_COLUMNS = ("Name",)

#: Cells whose sort position was guessed are tinted, rather than interrupting entry with a
#: dialog. The queue in the status bar is how the user finds them later.
REVIEW_BACKGROUND = QColor(255, 249, 219)
DELETED_FOREGROUND = QColor(150, 150, 150)


class SpecimenTableModel(QAbstractTableModel):
    """Rows are specimens; columns are the fields a subcollection shows."""

    error = Signal(str)
    contents_changed = Signal()

    def __init__(
        self,
        service: CollectionService,
        undo_stack: QUndoStack,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.undo = undo_stack
        self.subcollection: Subcollection | None = None
        self.show_trash = False
        self.search_term = ""
        self._specimens: list[Specimen] = []
        self._columns: list[Column] = []
        self._fields: list[FieldDefinition] = []
        self._grid: dict[tuple[int, int], str] = {}
        self._flagged: set[tuple[int, int]] = set()
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

    # -- loading ----------------------------------------------------------

    def set_subcollection(self, subcollection: Subcollection | None) -> None:
        """``None`` means the master view: every subcollection merged."""
        self.subcollection = subcollection
        self.refresh()

    def set_search(self, term: str) -> None:
        self.search_term = term.strip()
        self.refresh()

    def set_show_trash(self, show: bool) -> None:
        self.show_trash = show
        self.refresh()

    def refresh(self) -> None:
        self.beginResetModel()
        try:
            self._columns = (
                self.service.columns_for(self.subcollection)
                if self.subcollection is not None
                else self.service.master_columns()
            )
            self._fields = [
                field
                for field in (
                    self.service.session.get(FieldDefinition, column.field_id)
                    for column in self._columns
                    if column.kind == "field"
                )
                if field is not None
            ]
            self._specimens = self._load_specimens()
            self._grid = self.service.value_grid(self._specimens, self._fields)
            self._flagged = self.service.review_flags(self._specimens, self._fields)
        finally:
            self.endResetModel()
        self.contents_changed.emit()

    def _load_specimens(self) -> list[Specimen]:
        if self.search_term:
            found = self.service.search(self.search_term, subcollection=self.subcollection)
            return [s for s in found if self.show_trash or s.deleted_at is None]

        if self._sort_column >= len(FIXED_COLUMNS):
            column = self._columns[self._sort_column - len(FIXED_COLUMNS)]
            if column.kind == "field":
                try:
                    return self.service.sorted_by_field(
                        column.key,
                        subcollection=self.subcollection,
                        descending=self._sort_order == Qt.SortOrder.DescendingOrder,
                        include_deleted=self.show_trash,
                    )
                except Exception as exc:  # a field type that cannot be sorted
                    self.error.emit(str(exc))

        # No field sort chosen: creation order, like a spreadsheet.
        query = self.service.live_specimens(
            self.subcollection, include_deleted=self.show_trash
        ).order_by(Specimen.id)
        specimens = list(self.service.session.scalars(query))
        if self._sort_column == 0:
            specimens.sort(
                key=lambda s: (s.display_name or "").lower(),
                reverse=self._sort_order == Qt.SortOrder.DescendingOrder,
            )
        return specimens

    # -- Qt model interface ----------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._specimens)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(FIXED_COLUMNS) + len(self._columns)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section < len(FIXED_COLUMNS):
                return FIXED_COLUMNS[section]
            return self._columns[section - len(FIXED_COLUMNS)].label
        return section + 1

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        column = self.column_at(index.column())
        if column is None:
            return base  # the name column is generated from a template
        if column.kind != "field":
            return base  # special systems have their own editors
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        specimen = self._specimens[index.row()]
        column = self.column_at(index.column())

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if column is None:
                # The name comes from the subcollection's naming template. Without one there
                # is nothing to render, so fall back to the row's identity rather than
                # leaving a column that looks broken.
                return specimen.display_name or specimen.inventory_code or f"#{specimen.id}"
            if column.kind != "field":
                return self._special_cell(specimen, column.kind)
            return self._grid.get((specimen.id, column.field_id), "")

        if (
            role == Qt.ItemDataRole.BackgroundRole
            and column is not None
            and column.kind == "field"
            and (specimen.id, column.field_id) in self._flagged
        ):
            return QBrush(REVIEW_BACKGROUND)

        if role == Qt.ItemDataRole.ForegroundRole and specimen.deleted_at is not None:
            return QBrush(DELETED_FOREGROUND)

        if role == Qt.ItemDataRole.FontRole and specimen.deleted_at is not None:
            font = QFont()
            font.setStrikeOut(True)
            return font

        if role == Qt.ItemDataRole.ToolTipRole:
            cell = (specimen.id, getattr(column, "field_id", None))
            if column is not None and cell in self._flagged:
                return (
                    "The sort position for this value was worked out automatically.\n"
                    "Right-click to confirm or change it."
                )
            if column is None and specimen.deleted_at is not None:
                return "In the Trash. Nothing is deleted permanently until you ask."
        return None

    def setData(
        self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        column = self.column_at(index.column())
        if column is None or column.kind != "field":
            return False

        specimen = self._specimens[index.row()]
        field = self.service.session.get(FieldDefinition, column.field_id)
        raw = "" if value is None else str(value).strip()
        if raw == self._grid.get((specimen.id, column.field_id), ""):
            return False

        problem = validate(self.service, field, raw)
        if problem:
            # Rejected edits never enter the undo history, and the message names the field
            # so the user is told what is wrong rather than simply refused.
            self.error.emit(f"{column.label}: {problem}")
            return False

        self.undo.push(SetValues(self.service, [(specimen.id, field.id, raw)]))
        self._reload_cells([(specimen.id, field.id)])
        self.dataChanged.emit(index, index)
        self.contents_changed.emit()
        return True

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort_column = column
        self._sort_order = order
        self.refresh()

    # -- helpers used by the view ----------------------------------------

    def column_at(self, section: int) -> Column | None:
        """The user column at a view section, or ``None`` for the generated name column."""
        if section < len(FIXED_COLUMNS):
            return None
        return self._columns[section - len(FIXED_COLUMNS)]

    def specimen_at(self, row: int) -> Specimen:
        return self._specimens[row]

    def field_at(self, section: int) -> FieldDefinition | None:
        column = self.column_at(section)
        if column is None or column.kind != "field":
            return None
        return self.service.session.get(FieldDefinition, column.field_id)

    def is_flagged(self, index: QModelIndex) -> bool:
        column = self.column_at(index.column())
        if column is None or column.kind != "field":
            return False
        return (self._specimens[index.row()].id, column.field_id) in self._flagged

    def apply_edits(self, edits: Sequence[tuple[QModelIndex, str]], description: str) -> int:
        """Apply many cell edits as one undoable step. Returns the number applied.

        Paste and fill-down use this so a block of changes is a single undo, which is what
        makes experimenting with a spreadsheet safe.
        """
        prepared: list[tuple[int, int, str]] = []
        rejected: list[str] = []
        for index, raw in edits:
            column = self.column_at(index.column())
            if column is None or column.kind != "field":
                continue
            field = self.service.session.get(FieldDefinition, column.field_id)
            specimen = self._specimens[index.row()]
            text = raw.strip()
            problem = validate(self.service, field, text)
            if problem:
                rejected.append(f"{column.label}: {problem}")
                continue
            prepared.append((specimen.id, field.id, text))

        if rejected:
            self.error.emit(f"{len(rejected)} value(s) skipped — {rejected[0]}")
        if not prepared:
            return 0

        self.undo.push(SetValues(self.service, prepared, text=description))
        self._reload_cells([(specimen_id, field_id) for specimen_id, field_id, _ in prepared])
        top_left = self.index(0, 0)
        bottom_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
        self.dataChanged.emit(top_left, bottom_right)
        self.contents_changed.emit()
        return len(prepared)

    def _reload_cells(self, cells: Sequence[tuple[int, int]]) -> None:
        """Refresh the cached display text and review flags for specific cells."""
        specimens = [s for s in self._specimens if s.id in {c[0] for c in cells}]
        fields = [f for f in self._fields if f.id in {c[1] for c in cells}]
        for cell in cells:
            self._grid.pop(cell, None)
            self._flagged.discard(cell)
        self._grid.update(self.service.value_grid(specimens, fields))
        self._flagged |= self.service.review_flags(specimens, fields)

    def cells_as_text(self, indexes: Sequence[QModelIndex]) -> list[list[str]]:
        """A rectangular block of display text for the clipboard."""
        if not indexes:
            return []
        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        return [
            [str(self.data(self.index(row, column), Qt.ItemDataRole.DisplayRole) or "")
             for column in columns]
            for row in rows
        ]
