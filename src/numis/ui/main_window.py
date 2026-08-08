"""The main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QToolBar,
    QWidget,
)
from sqlalchemy import select

from ..db import Library, create_library, open_library
from ..models import Subcollection
from ..services import CollectionService
from .commands import AddSpecimens, DeleteSpecimens, SetSortValue
from .fields_dialog import ManageFieldsDialog, NewFieldDialog
from .sheet_view import SheetView
from .table_model import SpecimenTableModel

MASTER_VIEW = "All subcollections"


class MainWindow(QMainWindow):
    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library
        self.session = library.session_factory()
        self.service = CollectionService(self.session)
        self.undo = QUndoStack(self)

        self.setWindowTitle(f"Collection — {library.path.name}")
        self.resize(1180, 620)

        # Widgets that other parts report into are built first, because refreshing the model
        # updates the status bar and the subcollection combo is read while doing so.
        # Permanent widgets, so transient messages ("Copied 6 cells", hints, errors) never
        # wipe out the row count. A count that vanishes after a few seconds is worse than none.
        self.count_label = QLabel()
        self.statusBar().addPermanentWidget(self.count_label)
        self.review_label = QLabel()
        self.statusBar().addPermanentWidget(self.review_label)

        self.subcollection_combo = QComboBox()
        self.subcollection_combo.setMinimumWidth(180)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search…  (try 通寶)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMaximumWidth(240)
        self.search_box.returnPressed.connect(self._search)

        self.model = SpecimenTableModel(self.service, self.undo, self)
        self.model.error.connect(self._show_error)
        self.model.contents_changed.connect(self._update_status)

        self.view = SheetView(self)
        self.view.setModel(self.model)
        self.view.sort_value_requested.connect(self._edit_sort_value)
        self.view.status.connect(lambda text: self.statusBar().showMessage(text, 4000))
        self.setCentralWidget(self.view)

        self.subcollection_combo.currentIndexChanged.connect(self._subcollection_changed)

        self._build_toolbar()
        self._build_menus()
        self._reload_subcollections()
        self._update_status()

    # -- construction -----------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)

        bar.addWidget(QLabel(" Viewing "))
        bar.addWidget(self.subcollection_combo)
        bar.addSeparator()

        self.add_row_action = QAction("Add row", self)
        self.add_row_action.setShortcut(QKeySequence("Ctrl+N"))
        self.add_row_action.triggered.connect(lambda: self._add_rows(1))
        bar.addAction(self.add_row_action)

        self.add_many_action = QAction("Add many…", self)
        self.add_many_action.triggered.connect(self._add_many)
        bar.addAction(self.add_many_action)

        self.delete_action = QAction("Delete rows", self)
        self.delete_action.triggered.connect(self._delete_rows)
        bar.addAction(self.delete_action)
        bar.addSeparator()

        self.columns_action = QAction("Columns…", self)
        self.columns_action.triggered.connect(self._manage_columns)
        bar.addAction(self.columns_action)
        bar.addSeparator()

        self.undo_action = self.undo.createUndoAction(self, "Undo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo.createRedoAction(self, "Redo")
        self.redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        bar.addAction(self.undo_action)
        bar.addAction(self.redo_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(self.search_box)

    def _search(self) -> None:
        self.model.set_search(self.search_box.text())
        term = self.search_box.text().strip()
        if term:
            self.statusBar().showMessage(
                f"{self.model.rowCount()} coin(s) matching “{term}”", 6000
            )

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("New library…", self)
        new_action.triggered.connect(self._new_library)
        open_action = QAction("Open library…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_library)
        backup_action = QAction("Back up now", self)
        backup_action.triggered.connect(self._backup)
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        for action in (new_action, open_action, backup_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        for action in (
            self.undo_action,
            self.redo_action,
        ):
            edit_menu.addAction(action)
        edit_menu.addSeparator()
        for action in (
            self.view.copy_action,
            self.view.paste_action,
            self.view.fill_down_action,
            self.view.clear_action,
        ):
            edit_menu.addAction(action)

        view_menu = self.menuBar().addMenu("&View")
        self.trash_action = QAction("Show Trash", self)
        self.trash_action.setCheckable(True)
        self.trash_action.toggled.connect(self.model.set_show_trash)
        view_menu.addAction(self.trash_action)
        review_action = QAction("Go to next unconfirmed sort value", self)
        review_action.triggered.connect(self._go_to_review)
        view_menu.addAction(review_action)

        collection_menu = self.menuBar().addMenu("&Collection")
        new_sub = QAction("New subcollection…", self)
        new_sub.triggered.connect(self._new_subcollection)
        new_field = QAction("New column…", self)
        new_field.triggered.connect(self._new_column)
        for action in (new_sub, new_field, self.columns_action):
            collection_menu.addAction(action)

    # -- subcollections ---------------------------------------------------

    def _reload_subcollections(self, keep: str | None = None) -> None:
        current = keep or self.subcollection_combo.currentText()
        self.subcollection_combo.blockSignals(True)
        self.subcollection_combo.clear()
        self.subcollection_combo.addItem(MASTER_VIEW, None)
        for subcollection in self.session.scalars(
            select(Subcollection).order_by(Subcollection.sort_order, Subcollection.name)
        ):
            self.subcollection_combo.addItem(subcollection.name, subcollection.id)
        index = self.subcollection_combo.findText(current)
        self.subcollection_combo.setCurrentIndex(max(index, 0))
        self.subcollection_combo.blockSignals(False)
        self._subcollection_changed()

    def _subcollection_changed(self) -> None:
        self.model.set_subcollection(self.current_subcollection())
        enabled = self.current_subcollection() is not None
        for action in (self.add_row_action, self.add_many_action, self.columns_action):
            action.setEnabled(enabled)
        if not enabled:
            self.statusBar().showMessage(
                "The master view merges every subcollection. Choose one to add rows or columns.",
                6000,
            )

    def current_subcollection(self) -> Subcollection | None:
        identifier = self.subcollection_combo.currentData()
        if identifier is None:
            return None
        return self.session.get(Subcollection, identifier)

    def _new_subcollection(self) -> None:
        name, accepted = QInputDialog.getText(self, "New subcollection", "Name")
        if not accepted or not name.strip():
            return
        subcollection = self.service.create_subcollection(name.strip())
        self.session.commit()
        self._reload_subcollections(keep=subcollection.name)

    # -- rows -------------------------------------------------------------

    def _add_rows(self, count: int) -> None:
        subcollection = self.current_subcollection()
        if subcollection is None:
            return
        self.undo.push(AddSpecimens(self.service, subcollection.id, count))
        self.model.refresh()
        self.statusBar().showMessage(f"Added {count} row(s)", 4000)

    def _add_many(self) -> None:
        subcollection = self.current_subcollection()
        if subcollection is None:
            return
        count, accepted = QInputDialog.getInt(
            self,
            "Add many rows",
            "How many identical rows?\n\n"
            "One row is one coin, so a lot of 47 coins becomes 47 rows you can then edit\n"
            "individually or all at once.",
            10,
            1,
            5000,
        )
        if accepted:
            self._add_rows(count)

    def _delete_rows(self) -> None:
        rows = sorted({index.row() for index in self.view.selectedIndexes()})
        if not rows:
            return
        specimen_ids = [self.model.specimen_at(row).id for row in rows]
        self.undo.push(DeleteSpecimens(self.service, specimen_ids))
        self.model.refresh()
        self.statusBar().showMessage(
            f"Moved {len(specimen_ids)} row(s) to the Trash — nothing is deleted permanently",
            6000,
        )

    # -- columns ----------------------------------------------------------

    def _new_column(self) -> None:
        dialog = NewFieldDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.create(
            self.current_subcollection()
        ):
            self.session.commit()
            self.model.refresh()

    def _manage_columns(self) -> None:
        subcollection = self.current_subcollection()
        if subcollection is None:
            return
        ManageFieldsDialog(self.service, subcollection, self).exec()
        self.model.refresh()

    # -- sort values ------------------------------------------------------

    def _edit_sort_value(self, index) -> None:  # noqa: ANN001
        value = self.view.ask_for_sort_value(index)
        if value is None:
            return
        specimen = self.model.specimen_at(index.row())
        field = self.model.field_at(index.column())
        self.undo.push(SetSortValue(self.service, specimen.id, field.id, value))
        self.model.refresh()
        self.statusBar().showMessage(f"Sort value set to {value:g}", 4000)

    def _go_to_review(self) -> None:
        for row in range(self.model.rowCount()):
            for column in range(self.model.columnCount()):
                index = self.model.index(row, column)
                if self.model.is_flagged(index):
                    self.view.setCurrentIndex(index)
                    self.view.scrollTo(index)
                    self.statusBar().showMessage(
                        "Right-click to confirm or change the sort value", 6000
                    )
                    return
        self.statusBar().showMessage("Nothing is waiting to be confirmed", 4000)

    # -- library ----------------------------------------------------------

    def _new_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose an empty folder for the library")
        if not path:
            return
        try:
            library = create_library(Path(path), exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "New library", str(exc))
            return
        self._switch_to(library)

    def _open_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open library folder")
        if not path:
            return
        try:
            library = open_library(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Open library", str(exc))
            return
        self._switch_to(library)

    def _switch_to(self, library: Library) -> None:
        self.session.close()
        self.library.close()
        self.library = library
        self.session = library.session_factory()
        self.service = CollectionService(self.session)
        self.undo.clear()
        self.model.service = self.service
        self.setWindowTitle(f"Collection — {library.path.name}")
        self._reload_subcollections(keep=MASTER_VIEW)

    def _backup(self) -> None:
        try:
            path = self.library.backup("manual")
        except Exception as exc:
            QMessageBox.warning(self, "Back up", str(exc))
            return
        self.statusBar().showMessage(f"Backed up to {path.name}", 6000)

    # -- status -----------------------------------------------------------

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    def _update_status(self) -> None:
        rows = self.model.rowCount()
        pending = len(self.service.needs_review(self.current_subcollection()))
        where = self.subcollection_combo.currentText()
        self.count_label.setText(f"  {rows} coin(s) in {where}  ")
        if pending:
            self.review_label.setText(f"  {pending} sort value(s) to confirm  ")
            self.review_label.setStyleSheet("background: #fff9db; padding: 2px 6px;")
        else:
            self.review_label.setText("")
            self.review_label.setStyleSheet("")

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self.session.commit()
        self.session.close()
        super().closeEvent(event)
