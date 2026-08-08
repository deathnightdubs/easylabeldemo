"""Managing columns.

Adding, removing, renaming and reordering columns is a normal, everyday action rather than an
advanced setting, because the whole premise is that the user builds their own schema.

Two removal options are offered, and the wording matters: taking a column out of a
subcollection keeps every value, and even deleting the column itself only archives it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..errors import NumisError
from ..fields import REGISTRY, get_field_type
from ..models import FieldDefinition, Subcollection
from ..services import CollectionService

#: Types offered when creating a column, in the order a collector is likely to want them.
OFFERED_TYPES = (
    "text",
    "date",
    "number",
    "weight",
    "dimension",
    "purity",
    "money",
    "angle",
    "rating",
    "boolean",
    "long_text",
)


def _slug(label: str) -> str:
    return "_".join(
        part for part in "".join(c.lower() if c.isalnum() else " " for c in label).split()
    )


class NewFieldDialog(QDialog):
    """Create a column: a label, a type, and whether it sorts numerically."""

    def __init__(self, service: CollectionService, parent: object | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("New column")
        self.setMinimumWidth(420)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Denomination")
        self.type_combo = QComboBox()
        for key in OFFERED_TYPES:
            self.type_combo.addItem(REGISTRY[key].label, key)
        self.numeric_sort = QCheckBox("Sort this column by a number rather than alphabetically")
        self.numeric_sort.setToolTip(
            "For values like '10 wen' or '1 mace', where the text does not sort usefully.\n"
            "A number is taken from the start of the value where possible, and you can set\n"
            "one by hand at any time by right-clicking a cell."
        )
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: #555;")

        form = QFormLayout()
        form.addRow("Name", self.label_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("", self.numeric_sort)
        form.addRow("", self.help)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.type_combo.currentIndexChanged.connect(self._update_help)
        self._update_help()

    def _update_help(self) -> None:
        key = self.type_combo.currentData()
        field_type = get_field_type(key)
        notes = {
            "text": "Any text. Filtering searches within it.",
            "date": "Accepts 1943, 1736-1795, c. 350 BC, AH 1256, 1930s or undated.",
            "weight": "Stored in grams; enter 27.15, 420 gr or 1 ozt.",
            "dimension": "Stored in millimetres; enter 38.1, 1 1/2 in or 3.81cm.",
            "purity": "Fineness. 0.900, 900, 90% and 22K all mean the same thing.",
            "money": "Amounts in the collection's currency.",
            "angle": "Die axis, in degrees or clock hours such as 6h.",
        }
        detail = notes.get(key, field_type.description or "")
        unit = f" Canonical unit: {field_type.canonical_unit}." if field_type.canonical_unit else ""
        self.help.setText(detail + unit)
        self.numeric_sort.setEnabled(key == "text")
        if key != "text":
            self.numeric_sort.setChecked(False)

    def create(self, subcollection: Subcollection | None) -> FieldDefinition | None:
        label = self.label_edit.text().strip()
        if not label:
            QMessageBox.warning(self, "New column", "Give the column a name.")
            return None
        key = _slug(label)
        if self.service.field_by_key(key) is not None:
            QMessageBox.warning(
                self, "New column", f"A column with the key “{key}” already exists."
            )
            return None

        data_type = self.type_combo.currentData()
        config = {"numeric_sort": True} if self.numeric_sort.isChecked() else {}
        field = self.service.create_field(key, label, data_type, config=config)
        if subcollection is not None:
            existing = self.service.columns_for(subcollection, table_only=False)
            self.service.show_field(
                subcollection, field, show_in_table=True, sort_order=len(existing)
            )
        return field


class ManageFieldsDialog(QDialog):
    """Reorder, rename, remove and add the columns of one subcollection."""

    def __init__(
        self,
        service: CollectionService,
        subcollection: Subcollection,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.subcollection = subcollection
        self.setWindowTitle(f"Columns — {subcollection.name}")
        self.resize(620, 420)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Shown as", "Type", "In table"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setColumnWidth(0, 260)

        explanation = QLabel(
            "The name here is how the column appears in this subcollection only. The same "
            "column can read “Ruler” in one subcollection and “Emperor” in another, and still "
            "be one column in the master view."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #555;")

        add = QPushButton("Add column…")
        up = QPushButton("Move up")
        down = QPushButton("Move down")
        remove = QPushButton("Remove from subcollection")
        delete = QPushButton("Delete column…")
        add.clicked.connect(self._add)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        remove.clicked.connect(self._remove)
        delete.clicked.connect(self._delete)

        buttons = QHBoxLayout()
        for widget in (add, up, down, remove, delete):
            buttons.addWidget(widget)
        buttons.addStretch()

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        close.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(explanation)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        layout.addWidget(close)

        self.table.itemChanged.connect(self._rename)
        self._reload()

    # -- contents ---------------------------------------------------------

    def _blocks(self):  # noqa: ANN201
        from sqlalchemy import select

        from ..models import SubcollectionBlock

        return list(
            self.service.session.scalars(
                select(SubcollectionBlock)
                .where(
                    SubcollectionBlock.subcollection_id == self.subcollection.id,
                    SubcollectionBlock.block_kind == "field",
                )
                .order_by(SubcollectionBlock.sort_order, SubcollectionBlock.id)
            )
        )

    def _reload(self) -> None:
        self.table.blockSignals(True)
        blocks = self._blocks()
        self.table.setRowCount(len(blocks))
        for row, block in enumerate(blocks):
            field = block.field
            label = QTableWidgetItem(block.display_label or field.label)
            label.setData(Qt.ItemDataRole.UserRole, block.id)
            type_item = QTableWidgetItem(get_field_type(field.data_type).label)
            type_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            shown = QTableWidgetItem()
            shown.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            shown.setCheckState(
                Qt.CheckState.Checked if block.show_in_table else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, label)
            self.table.setItem(row, 1, type_item)
            self.table.setItem(row, 2, shown)
        self.table.blockSignals(False)

    def _selected_block(self):  # noqa: ANN201
        row = self.table.currentRow()
        if row < 0:
            return None
        blocks = self._blocks()
        return blocks[row] if row < len(blocks) else None

    # -- actions ----------------------------------------------------------

    def _add(self) -> None:
        dialog = NewFieldDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.create(self.subcollection):
            self.service.session.commit()
            self._reload()

    def _rename(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            block = self._blocks()[item.row()]
            text = item.text().strip()
            block.display_label = text or None
            self.service.session.commit()
        elif item.column() == 2:
            block = self._blocks()[item.row()]
            block.show_in_table = int(item.checkState() == Qt.CheckState.Checked)
            self.service.session.commit()

    def _move(self, delta: int) -> None:
        blocks = self._blocks()
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(blocks):
            return
        blocks[row].sort_order, blocks[target].sort_order = target, row
        for index, block in enumerate(sorted(blocks, key=lambda b: b.sort_order)):
            block.sort_order = index
        self.service.session.commit()
        self._reload()
        self.table.setCurrentCell(target, 0)

    def _remove(self) -> None:
        block = self._selected_block()
        if block is None:
            return
        field = block.field
        count = self.service.count_values(field)
        confirmed = QMessageBox.question(
            self,
            "Remove from subcollection",
            f"Remove “{block.display_label or field.label}” from {self.subcollection.name}?\n\n"
            f"Its {count} value(s) are kept. Adding the column back restores them, and the "
            "column stays available to other subcollections.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if confirmed == QMessageBox.StandardButton.Ok:
            self.service.hide_field(self.subcollection, field)
            self.service.session.commit()
            self._reload()

    def _delete(self) -> None:
        block = self._selected_block()
        if block is None:
            return
        field = block.field
        count = self.service.count_values(field)
        confirmed = QMessageBox.question(
            self,
            "Delete column",
            f"Archive the column “{field.label}” everywhere?\n\n"
            f"Its {count} value(s) are kept and hidden, and it can be restored. Nothing is "
            "destroyed.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if confirmed == QMessageBox.StandardButton.Ok:
            try:
                self.service.archive_field(field)
                self.service.hide_field(self.subcollection, field)
                self.service.session.commit()
            except NumisError as exc:
                QMessageBox.warning(self, "Delete column", str(exc))
            self._reload()
