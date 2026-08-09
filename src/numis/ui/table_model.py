"""The spreadsheet grid.

A ``QAbstractTableModel`` over the collection. Values are loaded a table at a time rather than
a cell at a time, and every edit becomes an undoable command rather than a direct write.

Two invariants keep the grid honest, both learned from bugs:

* **A refresh is all-or-nothing.** Columns, rows and cached values are built into locals and
  only then assigned. A failure part-way through used to leave the new columns beside the old
  rows, which showed one subcollection's coins under another's headings.
* **A sort is remembered by which column it was, not by its position.** Positions mean
  different things in different subcollections, so remembering an index silently re-sorted by
  an unrelated column, or pointed past the end of a narrower subcollection entirely.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QUndoStack

from ..constants import DISPOSED_STATUSES, SPECIMEN_STATUSES
from ..errors import NumisError
from ..filters import NO_FILTER, FilterGroup, SortKey, add_sort_key, describe_sort
from ..models import FieldDefinition, Specimen, Subcollection
from ..services import CollectionService, Column
from .commands import SetDisplayName, SetInventoryCode, SetStatus, SetValues, validate

#: The identity columns, shown before the user's own fields. All are editable.
ID_COLUMN, NAME_COLUMN, SUBCOLLECTION_COLUMN, STATUS_COLUMN = 0, 1, 2, 3
FIXED_COLUMNS = ("ID", "Name", "Subcollection", "Status")
FIXED_KEYS = ("__id__", "__name__", "__subcollection__", "__status__")

#: Cells whose sort position was guessed are tinted, rather than interrupting entry with a
#: dialog. The queue in the status bar is how the user finds them later.
REVIEW_BACKGROUND = QColor(255, 249, 219)
DELETED_FOREGROUND = QColor(150, 150, 150)


def sort_target(column: Column) -> str:
    """The filter/sort target naming a grid column.

    Columns are identified by key within a subcollection, but a sort has to be storable in a
    saved view and comparable across subcollections, so a field becomes ``field:<key>`` and a
    special system is named by its kind.
    """
    return f"field:{column.key}" if column.kind == "field" else column.kind


class SpecimenTableModel(QAbstractTableModel):
    """Rows are specimens; columns are the fields a subcollection shows."""

    error = Signal(str)
    contents_changed = Signal()
    #: Emitted when a remembered sort no longer applies, so the view can clear its indicator.
    sort_cleared = Signal()

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
        self.show_disposed = False
        self.search_term = ""
        self._specimens: list[Specimen] = []
        self._columns: list[Column] = []
        self._fields: list[FieldDefinition] = []
        self._grid: dict[tuple[int, int], str] = {}
        self._flagged: set[tuple[int, int]] = set()
        self._sort: tuple[SortKey, ...] = ()
        self.filters: FilterGroup = NO_FILTER
        #: Set by the window: asked before an identifier held by a deleted coin is reused.
        self.confirm_reuse: Callable[[str, Specimen], bool] | None = None

    # -- loading ----------------------------------------------------------

    @property
    def sort_keys(self) -> tuple[SortKey, ...]:
        """How the grid is ordered, most significant first. Empty means creation order."""
        return self._sort

    @property
    def sort_key(self) -> str | None:
        """The primary sort target, or ``None`` for creation order."""
        return self._sort[0].target if self._sort else None

    def set_subcollection(self, subcollection: Subcollection | None) -> None:
        """``None`` means the master view: every subcollection merged."""
        self.subcollection = subcollection
        self.refresh()

    def set_search(self, term: str) -> None:
        self.search_term = term.strip()
        self.refresh()

    def set_filters(self, filters: FilterGroup | None) -> None:
        self.filters = filters or NO_FILTER
        self.refresh()

    def set_sort_keys(self, keys: Sequence[SortKey]) -> None:
        self._sort = tuple(keys)
        self.refresh()

    def set_show_trash(self, show: bool) -> None:
        self.show_trash = show
        self.refresh()

    def set_show_disposed(self, show: bool) -> None:
        """Whether coins that have left the collection are listed."""
        self.show_disposed = show
        self.refresh()

    def refresh(self) -> None:
        """Reload everything shown. Atomic: either all of it updates, or none of it does."""
        columns = (
            self.service.columns_for(self.subcollection)
            if self.subcollection is not None
            else self.service.master_columns()
        )
        fields = [
            field
            for field in (
                self.service.session.get(FieldDefinition, column.field_id)
                for column in columns
                if column.kind == "field"
            )
            if field is not None
        ]

        # A remembered sort only survives while its columns are still present here.
        available = self._available_targets(columns)
        kept = tuple(key for key in self._sort if key.target in available)
        if kept != self._sort:
            self._sort = kept
            self.sort_cleared.emit()

        specimens = self._load_specimens(columns)
        grid = self.service.value_grid(specimens, fields)
        flagged = self.service.review_flags(specimens, fields)

        self.beginResetModel()
        self._columns, self._fields = columns, fields
        self._specimens, self._grid, self._flagged = specimens, grid, flagged
        self.endResetModel()
        self.contents_changed.emit()

    def _available_targets(self, columns: Sequence[Column]) -> set[str]:
        """The sort targets the columns on screen can offer."""
        targets = set(FIXED_KEYS)
        for column in columns:
            targets.add(sort_target(column))
        return targets

    def _catalogue_choices(self, columns: Sequence[Column]) -> dict[str, str]:
        """Which catalogue a catalogue column should sort by, when it shows only one."""
        choices: dict[str, str] = {}
        for column in columns:
            if (
                column.kind == "catalogues"
                and column.display.mode == "only"
                and column.display.only
            ):
                choices["catalogues"] = column.display.only
        return choices

    def _load_specimens(self, columns: Sequence[Column]) -> list[Specimen]:
        """One query for filtering, searching and sorting together.

        These used to be three separate paths, and a search silently discarded the sort while a
        sorted column silently ignored the filter.
        """
        try:
            return self.service.query_specimens(
                self.subcollection,
                filters=self.filters,
                sort=self._sort,
                term=self.search_term,
                include_deleted=self.show_trash,
                include_disposed=self.show_disposed,
                catalogues=self._catalogue_choices(columns),
            )
        except NumisError as exc:
            # A filter or sort that cannot be carried out must not empty the grid without
            # explanation, so the request is reported and the unsorted list still shown.
            self.error.emit(str(exc))
            return self.service.query_specimens(
                self.subcollection,
                term=self.search_term,
                include_deleted=self.show_trash,
                include_disposed=self.show_disposed,
            )

    def _subcollection_name(self, specimen: Specimen) -> str:
        subcollection = self.service.session.get(Subcollection, specimen.subcollection_id)
        return subcollection.name if subcollection else ""

    # -- Qt model interface ----------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._specimens)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(FIXED_COLUMNS) + len(self._columns)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation == Qt.Orientation.Vertical:
            return section + 1 if role == Qt.ItemDataRole.DisplayRole else None
        if role == Qt.ItemDataRole.DisplayRole:
            if section < len(FIXED_COLUMNS):
                return FIXED_COLUMNS[section]
            return self._columns[section - len(FIXED_COLUMNS)].label
        if role == Qt.ItemDataRole.ToolTipRole and section >= len(FIXED_COLUMNS):
            column = self.column_at(section)
            if column is not None and column.kind != "field":
                return (
                    f"{column.display.describe(column.kind)}.\n"
                    "Right-click the header to change what this column shows."
                )
            return None
        if role == Qt.ItemDataRole.ToolTipRole and section < len(FIXED_COLUMNS):
            return {
                ID_COLUMN: "A unique identifier, assigned automatically and editable.",
                NAME_COLUMN: "Rendered from the subcollection's naming template, "
                "or type your own.",
                SUBCOLLECTION_COLUMN: "Type another subcollection's name to move the coin.",
                STATUS_COLUMN: "owned, sold, traded, gifted, lost, stolen, ordered, "
                "on_loan, returned or wanted. Sold coins are hidden unless "
                "View > Show sold and disposed is on.",
            }[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        column = self.column_at(index.column())
        if column is None:
            return base | Qt.ItemFlag.ItemIsEditable  # ID, Name and Subcollection
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
                return self._fixed_value(specimen, index.column())
            if column.kind != "field":
                return self.service.special_cell(specimen, column.kind, column.display)
            return self._grid.get((specimen.id, column.field_id), "")

        if (
            role == Qt.ItemDataRole.BackgroundRole
            and column is not None
            and column.kind == "field"
            and (specimen.id, column.field_id) in self._flagged
        ):
            return QBrush(REVIEW_BACKGROUND)

        if role == Qt.ItemDataRole.ForegroundRole and (
            specimen.deleted_at is not None or specimen.status in DISPOSED_STATUSES
        ):
            return QBrush(DELETED_FOREGROUND)

        if role == Qt.ItemDataRole.FontRole:
            if specimen.deleted_at is not None:
                font = QFont()
                font.setStrikeOut(True)
                return font
            if specimen.status in DISPOSED_STATUSES:
                # Italic, not struck through: the coin was yours and its history stands; it
                # simply is not in the collection any more.
                font = QFont()
                font.setItalic(True)
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
            if column is None and specimen.status in DISPOSED_STATUSES:
                return (
                    f"Marked as {specimen.status}. Still in your collection's history, "
                    "and shown because 'Show sold and disposed' is on."
                )
        return None

    def _fixed_value(self, specimen: Specimen, section: int) -> str:
        if section == ID_COLUMN:
            return specimen.inventory_code or ""
        if section == NAME_COLUMN:
            return specimen.display_name or ""
        if section == STATUS_COLUMN:
            return specimen.status
        return self._subcollection_name(specimen)

    def _special_cell(self, specimen: Specimen, kind: str) -> str:
        """A read-only summary of a special system, rendered with that column's settings.

        Kept as a thin wrapper because the rules belong in the service, where they can be tested
        without Qt and reused by exports and label templates.
        """
        column = next((c for c in self._columns if c.kind == kind), None)
        display = column.display if column is not None else None
        return self.service.special_cell(specimen, kind, display)

    def setData(
        self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        specimen = self._specimens[index.row()]
        raw = "" if value is None else str(value).strip()
        column = self.column_at(index.column())

        if column is None:
            return self._set_fixed(specimen, index, raw)
        if column.kind != "field":
            return False
        if raw == self._grid.get((specimen.id, column.field_id), ""):
            return False

        field = self.service.session.get(FieldDefinition, column.field_id)
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

    def _set_fixed(self, specimen: Specimen, index: QModelIndex, raw: str) -> bool:
        """Edit one of the identity columns."""
        section = index.column()

        if section == ID_COLUMN:
            if raw == (specimen.inventory_code or ""):
                return False
            owner = self.service.inventory_code_owner(raw) if raw else None
            reuse = False
            if owner is not None and owner.id != specimen.id:
                if owner.deleted_at is None:
                    self.error.emit(
                        f"ID {raw!r} is already used by {owner.display_name or 'another coin'}"
                    )
                    return False
                # The holder is in the Trash, so reuse is allowed once confirmed.
                if self.confirm_reuse is None or not self.confirm_reuse(raw, owner):
                    return False
                reuse = True
            self.undo.push(
                SetInventoryCode(self.service, specimen.id, raw or None, reuse_from_trash=reuse)
            )

        elif section == NAME_COLUMN:
            if raw == (specimen.display_name or ""):
                return False
            self.undo.push(SetDisplayName(self.service, specimen.id, raw))

        elif section == STATUS_COLUMN:
            if raw == specimen.status:
                return False
            if raw not in SPECIMEN_STATUSES:
                self.error.emit(
                    f"{raw!r} is not a status. Use one of: {', '.join(SPECIMEN_STATUSES)}"
                )
                return False
            self.undo.push(SetStatus(self.service, [specimen.id], raw))

        else:
            target = self.service.subcollection_by_name(raw)
            if target is None:
                self.error.emit(f"There is no subcollection called {raw!r}")
                return False
            if target.id == specimen.subcollection_id:
                return False
            from .commands import MoveSpecimens

            self.undo.push(MoveSpecimens(self.service, [specimen.id], target.id))

        self.refresh()
        return True

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
        *,
        additional: bool = False,
    ) -> None:
        """Sort by a column, or add it as a tie-breaker behind the keys already chosen."""
        target = self.target_at(column)
        if target is None:
            return
        self._sort = add_sort_key(
            self._sort,
            target,
            descending=order == Qt.SortOrder.DescendingOrder,
            additional=additional,
        )
        self.refresh()

    # -- helpers used by the view ----------------------------------------

    def target_at(self, section: int) -> str | None:
        """The sort/filter target for a column position."""
        if section < len(FIXED_COLUMNS):
            return FIXED_KEYS[section]
        column = self.column_at(section)
        return sort_target(column) if column is not None else None

    def section_of(self, target: str) -> int | None:
        """Where a sort target currently sits, so the view can mark the right header."""
        if target in FIXED_KEYS:
            return FIXED_KEYS.index(target)
        for offset, column in enumerate(self._columns):
            if sort_target(column) == target:
                return offset + len(FIXED_COLUMNS)
        return None

    def sort_summary(self) -> str:
        """How the grid is ordered, in words."""
        labels = {FIXED_KEYS[index]: FIXED_COLUMNS[index] for index in range(len(FIXED_KEYS))}
        for column in self._columns:
            labels[sort_target(column)] = column.label
        return describe_sort(self._sort, labels)

    def column_labels(self) -> dict[str, str]:
        """Every sortable/filterable target on screen, with the label the user sees."""
        labels = {FIXED_KEYS[index]: FIXED_COLUMNS[index] for index in range(len(FIXED_KEYS))}
        for column in self._columns:
            labels[sort_target(column)] = column.label
        return labels

    def key_at(self, section: int) -> str | None:
        """A stable identifier for a column position, used to remember sorting."""
        if section < len(FIXED_COLUMNS):
            return FIXED_KEYS[section]
        offset = section - len(FIXED_COLUMNS)
        if offset >= len(self._columns):
            return None
        return self._columns[offset].key

    def column_at(self, section: int) -> Column | None:
        """The user column at a view section, or ``None`` for an identity column."""
        if section < len(FIXED_COLUMNS):
            return None
        offset = section - len(FIXED_COLUMNS)
        if offset >= len(self._columns):
            return None
        return self._columns[offset]

    def specimen_at(self, row: int) -> Specimen:
        return self._specimens[row]

    def row_of(self, specimen_id: int) -> int | None:
        """Where a coin currently sits, so a selection can survive a refresh."""
        for row, specimen in enumerate(self._specimens):
            if specimen.id == specimen_id:
                return row
        return None

    def field_at(self, section: int) -> FieldDefinition | None:
        column = self.column_at(section)
        if column is None or column.kind != "field":
            return None
        return self.service.session.get(FieldDefinition, column.field_id)

    def is_flagged(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return False
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
            [
                str(self.data(self.index(row, column), Qt.ItemDataRole.DisplayRole) or "")
                for column in columns
            ]
            for row in rows
        ]



