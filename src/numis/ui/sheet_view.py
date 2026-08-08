"""The table widget and its spreadsheet behaviours.

Qt's table view gives cell editing, keyboard navigation and column reordering. The behaviours
a spreadsheet user expects on top of that — Enter moving down, block copy and paste, fill down,
clearing a selection — are implemented here.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QMenu,
    QTableView,
)

from . import clipboard
from .table_model import SpecimenTableModel


class SheetView(QTableView):
    """A table that behaves the way a spreadsheet user expects."""

    sort_value_requested = Signal(QModelIndex)
    status = Signal(str)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        header = self.horizontalHeader()
        header.setSectionsMovable(True)
        # Interactive sizing on purpose: automatic resize-to-contents is the usual cause of
        # sluggish scrolling on large tables, because it measures every row.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(140)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        self.verticalHeader().setDefaultSectionSize(24)
        # Hidden because the grid has a real ID column; two different numbers down the left
        # edge invites the reader to mistake the row position for the coin's identifier.
        self.verticalHeader().setVisible(False)

        self.move_requested_action: QAction | None = None
        self._build_actions()

    def setModel(self, model: object) -> None:
        super().setModel(model)
        # Sorting is available, but nothing is sorted until the user clicks a header, so the
        # indicator must not claim otherwise.
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        if hasattr(model, "contents_changed"):
            model.contents_changed.connect(self._fit_columns)
            self._fit_columns()

    def clear_sort_indicator(self) -> None:
        """Stop claiming a sort that no longer applies to the columns on screen."""
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def _fit_columns(self) -> None:
        """Size columns to their contents, but only for tables small enough for it to be
        cheap. Measuring every row is the usual cause of a sluggish grid."""
        if self.model() is not None and self.model().rowCount() <= 500:
            self.resizeColumnsToContents()

    # -- actions ----------------------------------------------------------

    def _build_actions(self) -> None:
        def action(text: str, shortcut: str, slot) -> QAction:  # noqa: ANN001
            item = QAction(text, self)
            item.setShortcut(QKeySequence(shortcut))
            item.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            item.triggered.connect(slot)
            self.addAction(item)
            return item

        self.copy_action = action("Copy", "Ctrl+C", self.copy_selection)
        self.paste_action = action("Paste", "Ctrl+V", self.paste_selection)
        self.fill_down_action = action("Fill down", "Ctrl+D", self.fill_down)
        self.clear_action = action("Clear contents", "Del", self.clear_selection)

    # -- spreadsheet behaviours -------------------------------------------

    def closeEditor(
        self, editor: object, hint: QAbstractItemDelegate.EndEditHint
    ) -> None:
        """Move down when an edit is committed with Enter, as a spreadsheet does."""
        if hint == QAbstractItemDelegate.EndEditHint.SubmitModelCache:
            super().closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            current = self.currentIndex()
            below = self.model().index(current.row() + 1, current.column())
            if below.isValid():
                self.setCurrentIndex(below)
            return
        super().closeEditor(editor, hint)

    def copy_selection(self) -> None:
        indexes = self.selectedIndexes()
        if not indexes:
            return
        block = self.sheet_model().cells_as_text(indexes)
        QGuiApplication.clipboard().setText(clipboard.encode(block))
        rows, columns = clipboard.shape(block)
        self.status.emit(f"Copied {rows} row(s) x {columns} column(s)")

    def paste_selection(self) -> None:
        """Paste a block, repeating it to fill the selection like a spreadsheet."""
        text = QGuiApplication.clipboard().text()
        block = clipboard.decode(text)
        if not block:
            return
        indexes = self.selectedIndexes()
        if not indexes:
            return

        model = self.sheet_model()
        top = min(index.row() for index in indexes)
        left = min(index.column() for index in indexes)
        rows, columns = clipboard.shape(block)

        if len(indexes) > 1:
            # A selection was made: fill exactly it, repeating the block as needed.
            height = max(index.row() for index in indexes) - top + 1
            width = max(index.column() for index in indexes) - left + 1
            block = clipboard.tile(block, height, width)
            rows, columns = height, width

        edits: list[tuple[QModelIndex, str]] = []
        for row_offset in range(rows):
            for column_offset in range(columns):
                index = model.index(top + row_offset, left + column_offset)
                if not index.isValid():
                    continue
                if not model.flags(index) & Qt.ItemFlag.ItemIsEditable:
                    continue
                edits.append((index, block[row_offset][column_offset]))

        applied = model.apply_edits(edits, f"paste {len(edits)} cells")
        if applied:
            self.status.emit(f"Pasted {applied} cell(s)")

    def fill_down(self) -> None:
        """Copy the top cell of each selected column down through the selection."""
        indexes = self.selectedIndexes()
        if len(indexes) < 2:
            return
        model = self.sheet_model()
        by_column: dict[int, list[QModelIndex]] = {}
        for index in indexes:
            by_column.setdefault(index.column(), []).append(index)

        edits: list[tuple[QModelIndex, str]] = []
        for column, cells in by_column.items():
            cells.sort(key=lambda index: index.row())
            source = str(model.data(cells[0], Qt.ItemDataRole.DisplayRole) or "")
            for index in cells[1:]:
                if model.flags(index) & Qt.ItemFlag.ItemIsEditable:
                    edits.append((index, source))
            del column

        applied = model.apply_edits(edits, f"fill down {len(edits)} cells")
        if applied:
            self.status.emit(f"Filled {applied} cell(s)")

    def clear_selection(self) -> None:
        model = self.sheet_model()
        edits = [
            (index, "")
            for index in self.selectedIndexes()
            if model.flags(index) & Qt.ItemFlag.ItemIsEditable
        ]
        applied = model.apply_edits(edits, f"clear {len(edits)} cells")
        if applied:
            self.status.emit(f"Cleared {applied} cell(s)")

    # -- context menus ----------------------------------------------------

    def _show_context_menu(self, position: object) -> None:
        index = self.indexAt(position)
        menu = QMenu(self)
        model = self.sheet_model()

        if index.isValid() and model.field_at(index.column()) is not None:
            field = model.field_at(index.column())
            if field.data_type in ("text", "date"):
                sort_action = menu.addAction("Set sort value…")
                sort_action.triggered.connect(lambda: self.sort_value_requested.emit(index))
                if model.is_flagged(index):
                    sort_action.setText("Confirm or change sort value…")
                menu.addSeparator()

        if self.move_requested_action is not None:
            menu.addAction(self.move_requested_action)
            menu.addSeparator()
        menu.addAction(self.copy_action)
        menu.addAction(self.paste_action)
        menu.addAction(self.fill_down_action)
        menu.addAction(self.clear_action)
        menu.exec(self.viewport().mapToGlobal(position))

    def _show_header_menu(self, position: object) -> None:
        header = self.horizontalHeader()
        section = header.logicalIndexAt(position)
        menu = QMenu(self)
        model = self.sheet_model()

        if section >= 0:
            hide = menu.addAction(f"Hide “{model.headerData(section, Qt.Orientation.Horizontal)}”")
            hide.triggered.connect(lambda: self.setColumnHidden(section, True))
        if any(self.isColumnHidden(index) for index in range(model.columnCount())):
            show = menu.addAction("Show all columns")
            show.triggered.connect(self._show_all_columns)
        menu.addSeparator()
        resize = menu.addAction("Fit columns to contents")
        resize.triggered.connect(self.resizeColumnsToContents)
        menu.exec(header.mapToGlobal(position))

    def _show_all_columns(self) -> None:
        for index in range(self.model().columnCount()):
            self.setColumnHidden(index, False)

    # -- helpers ----------------------------------------------------------

    def sheet_model(self) -> SpecimenTableModel:
        return self.model()  # type: ignore[return-value]

    def ask_for_sort_value(self, index: QModelIndex) -> float | None:
        """Prompt for a sort position, prefilled with whatever is currently used."""
        model = self.sheet_model()
        field = model.field_at(index.column())
        specimen = model.specimen_at(index.row())
        columns = model.service.raw_columns(specimen, field) or {}
        current = columns.get("sort_value")
        shown = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "")

        value, accepted = QInputDialog.getDouble(
            self,
            "Sort value",
            f"Where should “{shown}” sort?\n\n"
            "This is the number the column is ordered by. The displayed text is untouched.",
            float(current) if current is not None else 0.0,
            -1_000_000.0,
            1_000_000.0,
            2,
        )
        return value if accepted else None
