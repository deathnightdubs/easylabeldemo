"""Building a filter.

One row per question: a column, something to ask of it, and a value. The operators offered come
from the column's own type, so a date column offers "is in the decade of" and a weight column
offers "is at least" without the dialog knowing anything about either.

Nesting is available but not in the way: the common case is a flat list matched with *all*, and
a group is added only when someone needs "bronze, and either Qianlong or Jiaqing". Rather than a
tree widget — which makes the simple case look complicated — a subgroup appears as an extra
section below the main list.

The count at the bottom updates as the filter is built, because the useful question when writing
a filter is not whether it is valid but whether it matches anything.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..constants import LINK_KINDS, SPECIMEN_STATUSES
from ..errors import NumisError
from ..filters import (
    OPERATOR_WORDS,
    Criterion,
    FilterError,
    FilterGroup,
    expected_values,
    operators_for,
)
from ..models import Subcollection
from ..services import CollectionService

#: The columns of the criteria table.
COLUMN, OPERATOR, VALUE, EXTRA = 0, 1, 2, 3
HEADERS = ("Column", "Test", "Value", "and")


class _CriteriaTable(QWidget):
    """A list of questions, and the widgets for editing them."""

    def __init__(
        self,
        targets: list[tuple[str, str]],
        data_types: dict[str, str],
        service: CollectionService,
        on_change,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.targets = targets
        self.data_types = data_types
        self.service = service
        self.on_change = on_change

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.setColumnWidth(COLUMN, 170)
        self.table.setColumnWidth(OPERATOR, 170)
        self.table.setColumnWidth(VALUE, 160)
        self.table.setColumnWidth(EXTRA, 110)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(140)

        add = QPushButton("Add a test")
        remove = QPushButton("Remove")
        add.clicked.connect(lambda: self.add_row())
        remove.clicked.connect(self._remove_row)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    # -- rows -------------------------------------------------------------

    def add_row(self, criterion: Criterion | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        column = QComboBox()
        for label, target in self.targets:
            column.addItem(label, target)
        operator = QComboBox()
        value = QLineEdit()
        extra = QLineEdit()
        extra.setPlaceholderText("and…")

        self.table.setCellWidget(row, COLUMN, column)
        self.table.setCellWidget(row, OPERATOR, operator)
        self.table.setCellWidget(row, VALUE, value)
        self.table.setCellWidget(row, EXTRA, extra)

        column.currentIndexChanged.connect(lambda _index, r=row: self._column_changed(r))
        operator.currentIndexChanged.connect(lambda _index, r=row: self._operator_changed(r))
        value.textChanged.connect(self._changed)
        extra.textChanged.connect(self._changed)

        if criterion is not None:
            index = column.findData(criterion.target)
            if index >= 0:
                column.setCurrentIndex(index)
        self._reload_operators(row)
        if criterion is not None:
            position = operator.findData(criterion.operator)
            if position >= 0:
                operator.setCurrentIndex(position)
            if criterion.values:
                value.setText(criterion.values[0])
            if len(criterion.values) > 1:
                extra.setText(criterion.values[1])
        self._operator_changed(row)
        self._changed()

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)
            self._changed()

    def _column_changed(self, row: int) -> None:
        self._reload_operators(row)
        self._operator_changed(row)
        self._changed()

    def _reload_operators(self, row: int) -> None:
        column = self.table.cellWidget(row, COLUMN)
        operator = self.table.cellWidget(row, OPERATOR)
        if column is None or operator is None:
            return
        target = column.currentData()
        operator.blockSignals(True)
        operator.clear()
        try:
            available = operators_for(target, self.data_types.get(target))
        except FilterError:
            available = ()
        for name in available:
            operator.addItem(OPERATOR_WORDS.get(name, name), name)
        operator.blockSignals(False)

    def _operator_changed(self, row: int) -> None:
        """Show only the value boxes this operator actually uses."""
        operator = self.table.cellWidget(row, OPERATOR)
        value = self.table.cellWidget(row, VALUE)
        extra = self.table.cellWidget(row, EXTRA)
        column = self.table.cellWidget(row, COLUMN)
        if None in (operator, value, extra, column):
            return
        wanted = expected_values(operator.currentData() or "")
        value.setEnabled(wanted != 0)
        extra.setEnabled(wanted == 2)
        if wanted == 0:
            value.clear()
        if wanted != 2:
            extra.clear()
        value.setPlaceholderText(
            "" if wanted == 0 else self._hint(column.currentData(), operator.currentData())
        )
        self._changed()

    def _hint(self, target: str, operator: str | None) -> str:
        if operator == "is_any_of":
            return "one per line, or comma separated"
        if target == "__status__":
            return ", ".join(SPECIMEN_STATUSES[:4]) + "…"
        if target == "links":
            return ", ".join(LINK_KINDS[:4]) + "…"
        if target == "grades":
            return "63, or a company's name"
        if target == "catalogues":
            return "KM, H, N#…"
        if target == "certifications":
            return "NGC, PCGS, CAC…"
        data_type = self.data_types.get(target, "")
        if data_type == "date":
            return "1875, 1736-1795…"
        if data_type in ("weight", "dimension", "money", "number", "purity", "angle"):
            return "a number"
        return ""

    def _changed(self) -> None:
        self.on_change()

    # -- result -----------------------------------------------------------

    def criteria(self) -> list[Criterion]:
        """The questions actually asked.

        A row whose value boxes are all empty is a placeholder rather than a question — the
        dialog opens with one so there is somewhere to type — so it is left out. A row that is
        *partly* filled is kept, because dropping it would quietly change the filter the user
        thinks they wrote; the summary reports what is missing instead.
        """
        found: list[Criterion] = []
        for row in range(self.table.rowCount()):
            column = self.table.cellWidget(row, COLUMN)
            operator = self.table.cellWidget(row, OPERATOR)
            value = self.table.cellWidget(row, VALUE)
            extra = self.table.cellWidget(row, EXTRA)
            if None in (column, operator, value, extra):
                continue
            target, name = column.currentData(), operator.currentData()
            if not target or not name:
                continue
            wanted = expected_values(name)
            if wanted == 0:
                values: tuple[str, ...] = ()
            elif wanted == 2:
                values = (value.text().strip(), extra.text().strip())
            elif wanted == -1:
                values = tuple(_split_list(value.text()))
            else:
                values = (value.text().strip(),)
            if wanted != 0 and not any(values):
                continue
            found.append(Criterion(target=target, operator=name, values=values))
        return found

    def load(self, criteria) -> None:
        while self.table.rowCount():
            self.table.removeRow(0)
        for criterion in criteria:
            self.add_row(criterion)


def _split_list(text: str) -> list[str]:
    """A list typed by hand, however the user chose to separate it."""
    parts = [part.strip() for chunk in text.splitlines() for part in chunk.split(",")]
    return [part for part in parts if part]


class FilterDialog(QDialog):
    """Build the filter the grid applies."""

    def __init__(
        self,
        service: CollectionService,
        subcollection: Subcollection | None,
        labels: dict[str, str],
        current: FilterGroup,
        parent: QWidget | None = None,
        *,
        include_deleted: bool = False,
        include_disposed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.subcollection = subcollection
        self.include_deleted = include_deleted
        self.include_disposed = include_disposed
        self.setWindowTitle("Filter")
        self.setMinimumWidth(680)

        targets, data_types = self._targets(labels)

        self.match = QComboBox()
        self.match.addItem("Match all of these", "all")
        self.match.addItem("Match any of these", "any")
        self.match.setCurrentIndex(0 if current.match == "all" else 1)

        self.criteria = _CriteriaTable(targets, data_types, service, self._update, self)

        self.group_match = QComboBox()
        self.group_match.addItem("and any of these", "any")
        self.group_match.addItem("and all of these", "all")
        self.subgroup = _CriteriaTable(targets, data_types, service, self._update, self)
        self.group_box = QGroupBox("Also")
        self.group_box.setCheckable(True)
        self.group_box.setToolTip(
            "For questions a single list cannot ask, such as bronze *and* either of two rulers."
        )
        inner = QVBoxLayout(self.group_box)
        inner.addWidget(self.group_match)
        inner.addWidget(self.subgroup)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("padding: 6px; background: #fbfbfb; border: 1px solid #ccc;")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Reset).setText("Clear filter")
        self.buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self._clear)

        layout = QVBoxLayout(self)
        layout.addWidget(self.match)
        layout.addWidget(self.criteria)
        layout.addWidget(self.group_box)
        layout.addWidget(self.summary)
        layout.addWidget(self.buttons)

        self._load(current)
        self._update()

    # -- construction -----------------------------------------------------

    def _targets(self, labels: dict[str, str]) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """Everything that can be asked about, labelled as the user sees it.

        Taken from the columns on screen plus the coin's own properties, rather than from every
        field in the library: filtering by a column you cannot see is how a grid ends up
        mysteriously empty.
        """
        data_types: dict[str, str] = {}
        entries: list[tuple[str, str]] = []
        for target, label in labels.items():
            entries.append((label, target))
            if target.startswith("field:"):
                field = self.service.field_by_key(target.removeprefix("field:"))
                if field is not None:
                    data_types[target] = field.data_type
        for target, label in (("__favourite__", "Favourite"),):
            if target not in labels:
                entries.append((label, target))
        return entries, data_types

    def _load(self, current: FilterGroup) -> None:
        self.criteria.load(current.criteria)
        nested = current.groups[0] if current.groups else None
        self.group_box.setChecked(nested is not None)
        if nested is not None:
            self.group_match.setCurrentIndex(0 if nested.match == "any" else 1)
            self.subgroup.load(nested.criteria)
        if not current.criteria:
            self.criteria.add_row()

    def _clear(self) -> None:
        self.criteria.load([])
        self.subgroup.load([])
        self.group_box.setChecked(False)
        self.criteria.add_row()
        self._update()

    # -- reactions --------------------------------------------------------

    def _update(self) -> None:
        group = self.group()
        if group.is_empty():
            self.summary.setText("No filter: every coin is listed.")
            return
        try:
            group.validate()
        except FilterError as exc:
            self.summary.setText(str(exc))
            return
        try:
            count = self.service.count_specimens(
                self.subcollection,
                filters=group,
                include_deleted=self.include_deleted,
                include_disposed=self.include_disposed,
            )
        except NumisError as exc:
            self.summary.setText(str(exc))
            return
        self.summary.setText(f"{group.describe(self._labels())}\n\n{count} coin(s) match.")

    def _labels(self) -> dict[str, str]:
        return {target: label for label, target in self.criteria.targets}

    def _accept_if_valid(self) -> None:
        group = self.group()
        try:
            group.validate(self._labels())
        except FilterError as exc:
            self.summary.setText(str(exc))
            return
        self.accept()

    # -- result -----------------------------------------------------------

    def group(self) -> FilterGroup:
        """The filter as built. Empty means no filtering."""
        nested: tuple[FilterGroup, ...] = ()
        if self.group_box.isChecked():
            inner = self.subgroup.criteria()
            if inner:
                nested = (
                    FilterGroup(
                        match=self.group_match.currentData() or "any",
                        criteria=tuple(inner),
                    ),
                )
        return FilterGroup(
            match=self.match.currentData() or "all",
            criteria=tuple(self.criteria.criteria()),
            groups=nested,
        )
