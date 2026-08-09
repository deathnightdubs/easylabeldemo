"""Defining and managing grade modifiers.

A modifier is anything that attaches to a grade: a problem, a sticker, a plus or star, a strike
designation, a colour, a contrast. What varies between them is not the arithmetic but how they
read, so each carries a full name, a short form for columns, whether it attaches with a space,
and — for stickers — who issues it.
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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..constants import GRADE_MODIFIER_KINDS
from ..errors import NumisError
from ..models import GradeModifier
from ..services import CollectionService

#: What each kind is for, and the default effect it has on a grade's value.
KIND_HELP = {
    "detail": ("A problem: cleaned, holed, damaged. Reads as its own name.", -0.4),
    "sticker": ("A separate endorsement from another company: CAC, WINGS, CNAS.", 0.15),
    "qualifier": ("A plus or a star. These attach with no space: MS63+", 0.25),
    "strike": ("A strike designation: Full Bands, Full Steps, Full Head.", 0.15),
    "colour": ("Copper colour: Red, Brown, Red-Brown.", 0.0),
    "contrast": ("Cameo, Deep Cameo, Prooflike.", 0.10),
}


class ModifierDialog(QDialog):
    """Create or edit one modifier."""

    def __init__(
        self,
        service: CollectionService,
        modifier: GradeModifier | None = None,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.modifier = modifier
        self.setWindowTitle("Edit modifier" if modifier else "New modifier")
        self.setMinimumWidth(460)

        self.label = QLineEdit()
        self.label.setPlaceholderText("Full Bands, Details, CAC sticker")
        self.abbreviation = QLineEdit()
        self.abbreviation.setPlaceholderText("FB, BN — what a column shows")
        self.kind = QComboBox()
        self.kind.addItems(GRADE_MODIFIER_KINDS)
        self.issuer = QLineEdit()
        self.issuer.setPlaceholderText("CAC, CACG, WINGS, CNAS")
        self.delta = QDoubleSpinBox()
        self.delta.setRange(-20.0, 20.0)
        self.delta.setDecimals(2)
        self.delta.setSingleStep(0.05)
        self.attach = QCheckBox("Attach with no space, as in MS63+")
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: #666;")

        form = QFormLayout()
        form.addRow("Name", self.label)
        form.addRow("Short form", self.abbreviation)
        form.addRow("Kind", self.kind)
        form.addRow("Issued by", self.issuer)
        form.addRow("Effect on the value", self.delta)
        form.addRow("", self.attach)
        form.addRow("", self.help)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.kind.currentTextChanged.connect(self._kind_changed)
        if modifier is not None:
            self.label.setText(modifier.label)
            self.abbreviation.setText(modifier.abbreviation or "")
            self.kind.setCurrentText(modifier.kind)
            self.issuer.setText(modifier.issuer or "")
            self.delta.setValue(modifier.normalised_delta)
            self.attach.setChecked(bool(modifier.attach_without_space))
        self._kind_changed(self.kind.currentText())

    def _kind_changed(self, kind: str) -> None:
        text, default = KIND_HELP.get(kind, ("", 0.0))
        extra = ""
        if kind == "sticker":
            extra = (
                "\n\nWhat this particular sticker says — green, gold — is recorded per coin, "
                "so one sticker modifier covers all of a company's."
            )
        if kind == "detail":
            extra = (
                "\n\nWhat the problem actually was is recorded per coin, so one Details "
                "modifier covers every kind of problem."
            )
        self.help.setText(text + extra)
        self.issuer.setEnabled(kind == "sticker")
        if self.modifier is None:
            self.delta.setValue(default)
            self.attach.setChecked(kind == "qualifier")

    def save(self) -> GradeModifier | None:
        label = self.label.text().strip()
        if not label:
            QMessageBox.warning(self, "Modifier", "Give the modifier a name.")
            return None
        values = {
            "label": label,
            "abbreviation": self.abbreviation.text().strip() or None,
            "kind": self.kind.currentText(),
            "issuer": self.issuer.text().strip() or None,
            "normalised_delta": self.delta.value(),
            "attach_without_space": self.attach.isChecked(),
        }
        try:
            if self.modifier is not None:
                return self.service.update_grade_modifier(self.modifier, **values)
            code = label.upper().replace(" ", "_")[:32]
            return self.service.create_grade_modifier(code=code, **values)
        except NumisError as exc:
            QMessageBox.warning(self, "Modifier", str(exc))
            return None


class ManageModifiersDialog(QDialog):
    """List every modifier, with editing and deletion.

    Deletion reports how many grades carry the modifier first, because removing one silently
    changes what those coins read as.
    """

    def __init__(self, service: CollectionService, parent: object | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Grade modifiers")
        self.resize(700, 380)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Shows as", "Kind", "Effect", "Used by"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)

        add = QPushButton("New…")
        edit = QPushButton("Edit…")
        delete = QPushButton("Delete…")
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit)
        delete.clicked.connect(self._delete)
        self.table.doubleClicked.connect(self._edit)

        buttons = QHBoxLayout()
        for widget in (add, edit, delete):
            buttons.addWidget(widget)
        buttons.addStretch()

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        close.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        layout.addWidget(close)
        self._reload()

    def _reload(self) -> None:
        modifiers = self.service.modifiers()
        self.table.setRowCount(len(modifiers))
        for row, modifier in enumerate(modifiers):
            cells = [
                modifier.label,
                modifier.short + ("  (no space)" if modifier.attach_without_space else ""),
                modifier.kind + (f"  · {modifier.issuer}" if modifier.issuer else ""),
                f"{modifier.normalised_delta:+g}",
                str(self.service.modifier_usage(modifier)),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, modifier.id)
                self.table.setItem(row, column, item)

    def _selected(self) -> GradeModifier | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return self.service.session.get(GradeModifier, item.data(Qt.ItemDataRole.UserRole))

    def _add(self) -> None:
        dialog = ModifierDialog(self.service, None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.save():
            self.service.session.commit()
            self._reload()

    def _edit(self) -> None:
        modifier = self._selected()
        if modifier is None:
            return
        dialog = ModifierDialog(self.service, modifier, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.save():
            self.service.session.commit()
            self._reload()

    def _delete(self) -> None:
        modifier = self._selected()
        if modifier is None:
            return
        used = self.service.modifier_usage(modifier)
        if used:
            answer = QMessageBox.question(
                self,
                "Delete modifier",
                f"“{modifier.label}” is on {used} grade(s).\n\n"
                "Deleting it removes it from those grades, which changes what they read as "
                "and what they are worth. Delete it anyway?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                return
        try:
            self.service.delete_grade_modifier(modifier, force=True)
            self.service.session.commit()
        except NumisError as exc:  # pragma: no cover - defensive
            self.service.session.rollback()
            QMessageBox.warning(self, "Delete modifier", str(exc))
        self._reload()
