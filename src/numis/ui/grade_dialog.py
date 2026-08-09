"""Recording a grade.

The user types what they want to see and what it counts as; the application works out the
comparable value from that plus the modifiers, and shows it read-only so it is obvious where the
number came from.

Nothing is chosen from a list of registered grades. That is what the previous version did, and
typing a grade that already existed on the scale hit a uniqueness constraint and took the whole
application down with it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from sqlalchemy import select

from .. import grading
from ..constants import GRADE_SOURCES
from ..models import GradeModifier, GradeScale, SpecimenGrade
from ..services import CollectionService
from .modifier_dialogs import ManageModifiersDialog, ModifierDialog

NEW_ENTRY = "New…"

#: What the detail column is asking for, by modifier kind.
DETAIL_PROMPT = {
    "detail": "what the problem was",
    "sticker": "green, gold…",
    "strike": "optional",
    "colour": "optional",
    "contrast": "optional",
    "qualifier": "optional",
}


class GradeDialog(QDialog):
    """Create or edit a grade."""

    def __init__(
        self,
        service: CollectionService,
        grade: SpecimenGrade | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.grade = grade
        self.setWindowTitle("Edit grade" if grade else "Add grade")
        self.setMinimumWidth(560)

        self.scale = QComboBox()
        self._reload_scales()

        self.label = QLineEdit()
        self.label.setPlaceholderText("MS63, gVF, 8 — whatever you want to see")

        self.base_value = QDoubleSpinBox()
        self.base_value.setRange(0.0, 100.0)
        self.base_value.setDecimals(2)
        self.base_value.setToolTip(
            "What this grade counts as on its own. Grades from different standards are "
            "compared on this, so they can be sorted together."
        )

        self.modifiers = QTableWidget(0, 2)
        self.modifiers.setHorizontalHeaderLabels(["Modifier", "What it says"])
        self.modifiers.horizontalHeader().setStretchLastSection(True)
        self.modifiers.setColumnWidth(0, 260)
        self.modifiers.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.modifiers.setMaximumHeight(150)

        new_modifier = QPushButton("New modifier…")
        manage = QPushButton("Manage modifiers…")
        new_modifier.clicked.connect(self._new_modifier)
        manage.clicked.connect(self._manage_modifiers)
        modifier_buttons = QHBoxLayout()
        modifier_buttons.addWidget(new_modifier)
        modifier_buttons.addWidget(manage)
        modifier_buttons.addStretch()

        self.calculated = QLabel("—")
        self.calculated.setStyleSheet("font-weight: bold;")
        self.calculated.setToolTip(
            "Worked out from the base value and the modifiers. This is what sorting compares."
        )
        self.preview = QLabel("—")
        self.preview.setStyleSheet("color: #444;")

        self.source = QComboBox()
        self.source.addItems(GRADE_SOURCES)
        self.assigned_by = QLineEdit()
        self.assigned_by.setPlaceholderText("NGC, a dealer's name, or your own")
        self.hide_assigned_by = QCheckBox("Hide who assigned this in columns")
        self.hide_assigned_by.setToolTip(
            "Useful when the same person graded many coins: worth recording, not worth a column."
        )
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("optional")

        form = QFormLayout()
        form.addRow("Scale", self.scale)
        form.addRow("Grade", self.label)
        form.addRow("Base value", self.base_value)
        form.addRow("Modifiers", self.modifiers)
        form.addRow("", modifier_buttons)
        form.addRow("Calculated value", self.calculated)
        form.addRow("Reads as", self.preview)
        form.addRow("Source", self.source)
        form.addRow("Assigned by", self.assigned_by)
        form.addRow("", self.hide_assigned_by)
        form.addRow("Notes", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.label.textChanged.connect(self._label_changed)
        self.base_value.valueChanged.connect(self._recalculate)
        self.modifiers.itemChanged.connect(lambda _item: self._recalculate())
        self.scale.currentIndexChanged.connect(self._recalculate)

        self._reload_modifiers()
        if grade is not None:
            self._load(grade)
        self._recalculate()

    # -- population -------------------------------------------------------

    def _reload_scales(self) -> None:
        current = self.scale.currentData() if self.scale.count() else None
        self.scale.clear()
        self.scale.addItem("(no scale)", None)
        for scale in self.service.session.scalars(select(GradeScale).order_by(GradeScale.code)):
            self.scale.addItem(f"{scale.code} — {scale.name}", scale.id)
        self.scale.addItem(NEW_ENTRY, "new")
        if current is not None:
            index = self.scale.findData(current)
            if index >= 0:
                self.scale.setCurrentIndex(index)

    def _reload_modifiers(self) -> None:
        checked = self.checked_modifiers()
        self.modifiers.blockSignals(True)
        available = self.service.modifiers()
        self.modifiers.setRowCount(len(available))
        for row, modifier in enumerate(available):
            name = QTableWidgetItem(
                f"{modifier.label}  ({modifier.kind}, {modifier.normalised_delta:+g})"
            )
            name.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            previous = checked.get(modifier.code)
            name.setCheckState(
                Qt.CheckState.Checked if previous is not None else Qt.CheckState.Unchecked
            )
            name.setData(Qt.ItemDataRole.UserRole, modifier.code)
            detail = QTableWidgetItem(previous or "")
            detail.setToolTip(DETAIL_PROMPT.get(modifier.kind, ""))
            self.modifiers.setItem(row, 0, name)
            self.modifiers.setItem(row, 1, detail)
        self.modifiers.blockSignals(False)

    def _load(self, grade: SpecimenGrade) -> None:
        index = self.scale.findData(grade.grade_scale_id)
        self.scale.setCurrentIndex(max(index, 0))
        self.label.setText(grade.grade_label)
        if grade.base_value is not None:
            self.base_value.setValue(grade.base_value)
        self.source.setCurrentText(grade.source)
        self.assigned_by.setText(grade.assigned_by or "")
        self.hide_assigned_by.setChecked(bool(grade.hide_assigned_by))
        self.notes.setText(grade.notes or "")

        details = {link.modifier.code: (link.detail or "") for link in grade.modifier_links}
        self.modifiers.blockSignals(True)
        for row in range(self.modifiers.rowCount()):
            code = self.modifiers.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if code in details:
                self.modifiers.item(row, 0).setCheckState(Qt.CheckState.Checked)
                self.modifiers.item(row, 1).setText(details[code])
        self.modifiers.blockSignals(False)

    # -- reactions --------------------------------------------------------

    def _label_changed(self, text: str) -> None:
        """Offer a value taken from the label, without overwriting a considered one."""
        if self.grade is None and self.base_value.value() == 0.0:
            suggested = grading.suggest_base_value(text)
            if suggested is not None:
                self.base_value.setValue(suggested)
        self._recalculate()

    def _recalculate(self) -> None:
        chosen = self.checked_modifiers()
        modifiers = [
            modifier for modifier in self.service.modifiers() if modifier.code in chosen
        ]
        total = grading.calculated_value(self.base_value.value(), modifiers)
        self.calculated.setText("—" if total is None else f"{total:g}")

        pieces = self.label.text().strip()
        for modifier in modifiers:
            detail = chosen.get(modifier.code) or ""
            piece = modifier.issuer or modifier.short if modifier.kind == "sticker" else (
                modifier.label if modifier.kind == "detail" else modifier.short
            )
            if detail:
                piece = f"{piece} {detail}" if modifier.kind != "detail" else f"{piece} — {detail}"
            pieces = pieces + piece if modifier.attach_without_space else f"{pieces} {piece}"
        self.preview.setText(pieces or "—")

    def _new_modifier(self) -> None:
        dialog = ModifierDialog(self.service, None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.save():
            self.service.session.commit()
            self._reload_modifiers()
            self._recalculate()

    def _manage_modifiers(self) -> None:
        ManageModifiersDialog(self.service, self).exec()
        self._reload_modifiers()
        self._recalculate()

    # -- results ----------------------------------------------------------

    def checked_modifiers(self) -> dict[str, str]:
        """The chosen modifiers, mapped to what each one says on this coin."""
        chosen: dict[str, str] = {}
        for row in range(self.modifiers.rowCount()):
            name = self.modifiers.item(row, 0)
            if name is None or name.checkState() != Qt.CheckState.Checked:
                continue
            detail = self.modifiers.item(row, 1)
            chosen[name.data(Qt.ItemDataRole.UserRole)] = (detail.text().strip() if detail else "")
        return chosen

    def modifier_pairs(self) -> list[tuple[str, str | None]]:
        return [(code, detail or None) for code, detail in self.checked_modifiers().items()]

    def resolve_scale(self) -> GradeScale | None:
        """The chosen scale, creating one if asked. ``None`` means no scale, which is allowed."""
        chosen = self.scale.currentData()
        if chosen is None:
            return None
        if chosen != "new":
            return self.service.session.get(GradeScale, chosen)

        code, accepted = QInputDialog.getText(
            self, "New grading scale", "Short code (SHELDON, ADJ, CN10):"
        )
        if not accepted or not code.strip():
            return None
        name, accepted = QInputDialog.getText(
            self, "New grading scale", "Full name:", text=code.strip()
        )
        if not accepted:
            return None
        scale = self.service.create_grade_scale(code.strip(), name.strip() or code.strip())
        self.service.session.commit()
        self._reload_scales()
        self.scale.setCurrentIndex(self.scale.findData(scale.id))
        return scale

    def validate(self) -> bool:
        if not self.label.text().strip():
            QMessageBox.warning(
                self, "Grade", "Type the grade you want to see, for example MS63 or gVF."
            )
            return False
        return True

    def values(self) -> dict[str, object]:
        return {
            "grade_label": self.label.text().strip(),
            "base_value": self.base_value.value(),
            "modifiers": self.modifier_pairs(),
            "source": self.source.currentText(),
            "assigned_by": self.assigned_by.text().strip() or None,
            "hide_assigned_by": self.hide_assigned_by.isChecked(),
        }

    def sticker_choice(self) -> tuple[GradeModifier, str | None] | None:
        """The chosen sticker, if any, for tying to a certification."""
        for code, detail in self.checked_modifiers().items():
            modifier = self.service.session.scalar(
                select(GradeModifier).where(GradeModifier.code == code)
            )
            if modifier is not None and modifier.kind == "sticker":
                return modifier, detail or None
        return None
