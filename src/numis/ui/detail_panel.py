"""Editors for the special systems.

Catalogue numbers, grades, certifications and links are multi-valued and structured, so they do
not fit in a single grid cell. They live in a panel beside the grid showing whichever coin is
selected.

Because the registries ship empty, every dialog here can create what it needs on the spot —
otherwise adding a first catalogue number would mean going elsewhere to define the catalogue,
and a blank slate would feel like a dead end rather than a clean start.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..constants import GRADE_MODIFIER_KINDS, GRADE_SOURCES, LINK_KINDS
from ..errors import NumisError
from ..models import (
    Catalog,
    CatalogReference,
    Certification,
    ExternalLink,
    GradeLevel,
    GradeModifier,
    GradeScale,
    GradingCompany,
    Specimen,
    SpecimenGrade,
)
from ..services import CollectionService

NEW_ENTRY = "New…"


def _combo_with_new(items: Sequence[tuple[str, object]]) -> QComboBox:
    """A picker whose last entry creates a new registry item."""
    combo = QComboBox()
    for label, value in items:
        combo.addItem(label, value)
    combo.addItem(NEW_ENTRY, None)
    return combo


class _Section(QWidget):
    """A titled list with Add and Remove buttons."""

    changed = Signal()

    def __init__(self, title: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMaximumHeight(96)

        heading = QLabel(f"<b>{title}</b>")
        self.hint = QLabel(hint)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #666;")

        self.add_button = QPushButton("Add…")
        self.remove_button = QPushButton("Remove")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.addWidget(heading)
        layout.addWidget(self.hint)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def selected_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def fill(self, rows: Sequence[tuple[str, int]]) -> None:
        self.list.clear()
        for label, identifier in rows:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            self.list.addItem(item)
        self.remove_button.setEnabled(bool(rows))

    def set_enabled(self, enabled: bool) -> None:
        self.add_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled and self.list.count() > 0)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class CatalogueReferenceDialog(QDialog):
    """Record a catalogue number, creating the catalogue if it does not exist yet."""

    def __init__(self, service: CollectionService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Add catalogue number")
        self.setMinimumWidth(400)

        catalogues = [
            (f"{catalog.code} — {catalog.name}", catalog.id)
            for catalog in service.session.scalars(select(Catalog).order_by(Catalog.code))
        ]
        self.catalogue = _combo_with_new(catalogues)
        self.number = QLineEdit()
        self.number.setPlaceholderText("2073, A54.2, 22.123")
        self.primary = QCheckBox("Use as this coin's main reference")
        self.primary.setChecked(not catalogues)

        form = QFormLayout()
        form.addRow("Catalogue", self.catalogue)
        form.addRow("Number", self.number)
        form.addRow("", self.primary)
        if not catalogues:
            note = QLabel("No catalogues exist yet — choose “New…” to add your first.")
            note.setStyleSheet("color: #666;")
            form.addRow("", note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def resolve_catalogue(self) -> Catalog | None:
        identifier = self.catalogue.currentData()
        if identifier is not None:
            return self.service.session.get(Catalog, identifier)
        code, accepted = QInputDialog.getText(
            self, "New catalogue", "Short code, as it is cited (KM, H, RIC, N#):"
        )
        if not accepted or not code.strip():
            return None
        name, accepted = QInputDialog.getText(
            self, "New catalogue", "Full name:", text=code.strip()
        )
        if not accepted:
            return None
        try:
            return self.service.create_catalog(code.strip(), name.strip() or code.strip())
        except NumisError as exc:  # pragma: no cover - defensive
            QMessageBox.warning(self, "New catalogue", str(exc))
            return None


class GradeDialog(QDialog):
    """Record a grade, creating the scale, its levels and any modifiers as needed."""

    def __init__(self, service: CollectionService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Add grade")
        self.setMinimumWidth(460)

        self.scale = _combo_with_new(
            [
                (f"{scale.code} — {scale.name}", scale.id)
                for scale in service.session.scalars(select(GradeScale).order_by(GradeScale.code))
            ]
        )
        self.level = QComboBox()
        self.source = QComboBox()
        self.source.addItems(GRADE_SOURCES)
        self.source.setCurrentText("self")
        self.assigned_by = QLineEdit()
        self.assigned_by.setPlaceholderText("NGC, a dealer's name, or your own")
        self.detail = QLineEdit()
        self.detail.setPlaceholderText("Cleaned, scratches — only for problem coins")
        self.modifiers = QListWidget()
        self.modifiers.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.modifiers.setMaximumHeight(80)
        self.new_modifier = QPushButton("New modifier…")
        self.primary = QCheckBox("Show this as the coin's grade")
        self.primary.setChecked(True)

        form = QFormLayout()
        form.addRow("Scale", self.scale)
        form.addRow("Grade", self.level)
        form.addRow("Modifiers", self.modifiers)
        form.addRow("", self.new_modifier)
        form.addRow("Problem", self.detail)
        form.addRow("Source", self.source)
        form.addRow("Assigned by", self.assigned_by)
        form.addRow("", self.primary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.scale.currentIndexChanged.connect(self._reload_levels)
        self.new_modifier.clicked.connect(self._create_modifier)
        self._reload_modifiers()
        self._reload_levels()

    def _reload_levels(self) -> None:
        self.level.clear()
        identifier = self.scale.currentData()
        if identifier is None:
            self.level.addItem("(define the scale first)", None)
            return
        levels = self.service.session.scalars(
            select(GradeLevel)
            .where(GradeLevel.grade_scale_id == identifier)
            .order_by(GradeLevel.normalised.desc())
        )
        for level in levels:
            self.level.addItem(f"{level.label}  ({level.normalised:g})", level.label)
        self.level.addItem(NEW_ENTRY, None)

    def _reload_modifiers(self) -> None:
        self.modifiers.clear()
        for modifier in self.service.session.scalars(
            select(GradeModifier).order_by(GradeModifier.kind, GradeModifier.code)
        ):
            item = QListWidgetItem(
                f"{modifier.label}  ({modifier.kind}, {modifier.normalised_delta:+g})"
            )
            item.setData(Qt.ItemDataRole.UserRole, modifier.code)
            self.modifiers.addItem(item)

    def _create_modifier(self) -> None:
        label, accepted = QInputDialog.getText(
            self, "New modifier", "Name (Details, CAC green, +, star):"
        )
        if not accepted or not label.strip():
            return
        kind, accepted = QInputDialog.getItem(
            self, "New modifier", "What kind is it?", list(GRADE_MODIFIER_KINDS), 0, False
        )
        if not accepted:
            return
        default = -0.4 if kind == "detail" else 0.15
        delta, accepted = QInputDialog.getDouble(
            self,
            "New modifier",
            "How far does it shift the grade?\n\n"
            "A small negative number keeps a problem grade just below its base grade;\n"
            "a small positive one lifts a coin slightly within its grade.",
            default,
            -5.0,
            5.0,
            2,
        )
        if not accepted:
            return
        code = label.strip().upper().replace(" ", "_")
        self.service.create_grade_modifier(code, label.strip(), kind, delta)
        self.service.session.commit()
        self._reload_modifiers()

    def resolve_scale(self) -> GradeScale | None:
        identifier = self.scale.currentData()
        if identifier is not None:
            return self.service.session.get(GradeScale, identifier)
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
        return self.service.create_grade_scale(code.strip(), name.strip() or code.strip())

    def resolve_level(self, scale: GradeScale) -> str | None:
        label = self.level.currentData()
        if label is not None:
            return label
        text, accepted = QInputDialog.getText(
            self, "New grade", f"Grade name on the {scale.code} scale (MS63, VF, 8):"
        )
        if not accepted or not text.strip():
            return None
        position, accepted = QInputDialog.getDouble(
            self,
            "New grade",
            "Where does it sit on the shared scale?\n\n"
            "This is the number every standard is compared on, so grades from different\n"
            "systems can be sorted together. Higher is better; 70 is a perfect coin.",
            50.0,
            0.0,
            100.0,
            1,
        )
        if not accepted:
            return None
        self.service.add_grade_level(scale, text.strip(), position)
        self.service.session.commit()
        return text.strip()

    def selected_modifier_codes(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.modifiers.selectedItems()]


class CertificationDialog(QDialog):
    """Record a certification, creating the grading company if needed."""

    def __init__(self, service: CollectionService, specimen: Specimen, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.service = service
        self.specimen = specimen
        self.setWindowTitle("Add certification")
        self.setMinimumWidth(420)

        self.company = _combo_with_new(
            [
                (f"{company.code} — {company.name}", company.id)
                for company in service.session.scalars(
                    select(GradingCompany).order_by(GradingCompany.code)
                )
            ]
        )
        self.number = QLineEdit()
        self.number.setPlaceholderText("optional — endorsements often have none")
        self.grade = QComboBox()
        self.grade.addItem("(none)", None)
        for grade in service.session.scalars(
            select(SpecimenGrade).where(SpecimenGrade.specimen_id == specimen.id)
        ):
            self.grade.addItem(grade.raw_text, grade.id)
        self.graded_on = QLineEdit()
        self.graded_on.setPlaceholderText("YYYY-MM-DD, optional")
        self.primary = QCheckBox("Show this as the coin's certification")
        self.primary.setChecked(True)

        form = QFormLayout()
        form.addRow("Company", self.company)
        form.addRow("Certificate no.", self.number)
        form.addRow("Grade on it", self.grade)
        form.addRow("Graded on", self.graded_on)
        form.addRow("", self.primary)
        note = QLabel(
            "Several certifications can be current at once, so adding an endorsement does not "
            "replace a grading company's own."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        form.addRow("", note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def resolve_company(self) -> GradingCompany | None:
        identifier = self.company.currentData()
        if identifier is not None:
            return self.service.session.get(GradingCompany, identifier)
        code, accepted = QInputDialog.getText(
            self, "New grading company", "Short code (NGC, PCGS, CAC, GBCA):"
        )
        if not accepted or not code.strip():
            return None
        name, accepted = QInputDialog.getText(
            self, "New grading company", "Full name:", text=code.strip()
        )
        if not accepted:
            return None
        return self.service.create_grading_company(code.strip(), name.strip() or code.strip())

    def parsed_date(self) -> date | None:
        text = self.graded_on.text().strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


class LinkDialog(QDialog):
    """Record a link to this coin's record elsewhere."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add link")
        self.setMinimumWidth(440)

        self.kind = QComboBox()
        self.kind.addItems(LINK_KINDS)
        self.label = QLineEdit()
        self.label.setPlaceholderText("optional, e.g. Zeno record")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("optional record id, lot number or page")

        form = QFormLayout()
        form.addRow("Kind", self.kind)
        form.addRow("Address", self.url)
        form.addRow("Label", self.label)
        form.addRow("Reference", self.reference)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


class SortValueDialog(QDialog):  # pragma: no cover - kept for symmetry, unused for now
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = QDoubleSpinBox()


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


class DetailPanel(QDockWidget):
    """Catalogue numbers, grades, certifications and links for the selected coin."""

    changed = Signal()

    def __init__(self, service: CollectionService, parent: QWidget | None = None) -> None:
        super().__init__("Selected coin", parent)
        self.service = service
        self.specimen: Specimen | None = None
        self.setObjectName("DetailPanel")
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )

        self.title = QLabel("No coin selected")
        self.title.setStyleSheet("font-weight: bold; padding: 4px 0;")
        self.title.setWordWrap(True)

        self.catalogues = _Section(
            "Catalogue numbers", "Any number of references, from any catalogue you define."
        )
        self.grades = _Section(
            "Grades", "Several are allowed; the one shown in the grid is the one you mark."
        )
        self.certifications = _Section(
            "Certifications", "A grading company's slab and a separate endorsement can coexist."
        )
        self.links = _Section("Links", "Zeno, an auction lot, a verification page, a paper.")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self.title)
        for section in (self.catalogues, self.grades, self.certifications, self.links):
            layout.addWidget(section)
        layout.addStretch()
        self.setWidget(body)

        self.catalogues.add_button.clicked.connect(self._add_catalogue_reference)
        self.grades.add_button.clicked.connect(self._add_grade)
        self.certifications.add_button.clicked.connect(self._add_certification)
        self.links.add_button.clicked.connect(self._add_link)
        self.catalogues.remove_button.clicked.connect(
            lambda: self._remove(CatalogReference, self.catalogues, "catalogue number")
        )
        self.grades.remove_button.clicked.connect(
            lambda: self._remove(SpecimenGrade, self.grades, "grade")
        )
        self.certifications.remove_button.clicked.connect(
            lambda: self._remove(Certification, self.certifications, "certification")
        )
        self.links.remove_button.clicked.connect(
            lambda: self._remove(ExternalLink, self.links, "link")
        )

        #: Set by the window so edits go through the undo stack.
        self.run_command: Callable[[object], None] | None = None
        self.show_specimen(None)

    # -- contents ---------------------------------------------------------

    def show_specimen(self, specimen: Specimen | None) -> None:
        self.specimen = specimen
        enabled = specimen is not None
        for section in (self.catalogues, self.grades, self.certifications, self.links):
            section.set_enabled(enabled)

        if specimen is None:
            self.title.setText("No coin selected")
            for section in (self.catalogues, self.grades, self.certifications, self.links):
                section.fill([])
            return

        name = specimen.display_name or "(unnamed)"
        self.title.setText(f"{specimen.inventory_code or '—'} · {name}")
        self.refresh()

    def refresh(self) -> None:
        specimen = self.specimen
        if specimen is None:
            return

        self.catalogues.fill(
            [
                (
                    f"{self.service.session.get(Catalog, reference.catalog_id).code} "
                    f"{reference.number_raw}" + ("  (main)" if reference.is_primary else ""),
                    reference.id,
                )
                for reference in self.service.references_for(specimen)
            ]
        )
        self.grades.fill(
            [
                (
                    f"{grade.raw_text}"
                    + (f"  — {grade.detail_note}" if grade.detail_note else "")
                    + f"  [{grade.assigned_by or grade.source}]"
                    + ("  (shown)" if grade.is_primary else ""),
                    grade.id,
                )
                for grade in specimen.grades
            ]
        )
        self.certifications.fill(
            [
                (
                    f"{certification.company.code} {certification.cert_number or ''}".strip()
                    + f"  · {certification.status}"
                    + ("  (shown)" if certification.is_primary else ""),
                    certification.id,
                )
                for certification in self.service.certification_history(specimen)
            ]
        )
        self.links.fill(
            [
                (f"{link.kind}: {link.label or link.url}", link.id)
                for link in specimen.links
            ]
        )

    def _apply(self, command: object) -> None:
        if self.run_command is not None:
            self.run_command(command)
        self.refresh()
        self.changed.emit()

    # -- adding -----------------------------------------------------------

    def _add_catalogue_reference(self) -> None:
        from .commands import AddChildRow

        specimen = self.specimen
        if specimen is None:
            return
        dialog = CatalogueReferenceDialog(self.service, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        catalogue = dialog.resolve_catalogue()
        number = dialog.number.text().strip()
        if catalogue is None or not number:
            return
        primary = dialog.primary.isChecked()
        specimen_id, catalogue_id = specimen.id, catalogue.id

        def build(service: CollectionService) -> object:
            return service.add_reference(
                service.session.get(Specimen, specimen_id),
                service.session.get(Catalog, catalogue_id),
                number,
                is_primary=primary,
            )

        try:
            self._apply(AddChildRow(self.service, "add catalogue number", build))
        except Exception as exc:  # duplicate number, mostly
            self.service.session.rollback()
            QMessageBox.warning(self, "Catalogue number", _readable(exc))

    def _add_grade(self) -> None:
        from .commands import AddChildRow

        specimen = self.specimen
        if specimen is None:
            return
        dialog = GradeDialog(self.service, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        scale = dialog.resolve_scale()
        if scale is None:
            return
        level_label = dialog.resolve_level(scale)
        if level_label is None:
            return

        specimen_id, scale_id = specimen.id, scale.id
        modifiers = dialog.selected_modifier_codes()
        detail = dialog.detail.text().strip() or None
        source = dialog.source.currentText()
        assigned_by = dialog.assigned_by.text().strip() or None
        primary = dialog.primary.isChecked()

        def build(service: CollectionService) -> object:
            return service.add_grade(
                service.session.get(Specimen, specimen_id),
                service.session.get(GradeScale, scale_id),
                level_label,
                modifiers=modifiers,
                source=source,
                assigned_by=assigned_by,
                detail_note=detail,
                is_primary=primary,
            )

        try:
            self._apply(AddChildRow(self.service, "add grade", build))
        except Exception as exc:
            self.service.session.rollback()
            QMessageBox.warning(self, "Grade", _readable(exc))

    def _add_certification(self) -> None:
        from .commands import AddChildRow

        specimen = self.specimen
        if specimen is None:
            return
        dialog = CertificationDialog(self.service, specimen, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        company = dialog.resolve_company()
        if company is None:
            return

        specimen_id, company_id = specimen.id, company.id
        number = dialog.number.text().strip() or None
        grade_id = dialog.grade.currentData()
        graded_on = dialog.parsed_date()
        primary = dialog.primary.isChecked()

        def build(service: CollectionService) -> object:
            grade = service.session.get(SpecimenGrade, grade_id) if grade_id else None
            return service.add_certification(
                service.session.get(Specimen, specimen_id),
                service.session.get(GradingCompany, company_id),
                cert_number=number,
                grade=grade,
                graded_on=graded_on,
                is_primary=primary,
            )

        try:
            self._apply(AddChildRow(self.service, "add certification", build))
        except Exception as exc:
            self.service.session.rollback()
            QMessageBox.warning(self, "Certification", _readable(exc))
        else:
            for warning in self.service.warnings[-1:]:
                if warning.code == "duplicate_cert_number":
                    QMessageBox.information(self, "Certification", warning.message)

    def _add_link(self) -> None:
        from .commands import AddChildRow

        specimen = self.specimen
        if specimen is None:
            return
        dialog = LinkDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.url.text().strip()
        if not url:
            return

        specimen_id = specimen.id
        kind = dialog.kind.currentText()
        label = dialog.label.text().strip() or None
        reference = dialog.reference.text().strip() or None

        def build(service: CollectionService) -> object:
            return service.add_link(
                service.session.get(Specimen, specimen_id),
                url,
                kind=kind,
                label=label,
                reference=reference,
            )

        self._apply(AddChildRow(self.service, "add link", build))

    # -- removing ---------------------------------------------------------

    def _remove(self, model: type, section: _Section, description: str) -> None:
        from .commands import DeleteChildRow

        row_id = section.selected_id()
        if row_id is None:
            return
        confirmed = QMessageBox.question(
            self,
            f"Remove {description}",
            f"Remove this {description}? It can be brought back with Undo.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if confirmed != QMessageBox.StandardButton.Ok:
            return
        self._apply(DeleteChildRow(self.service, f"remove {description}", model, row_id))


def _readable(error: Exception) -> str:
    """Turn a database error into something worth reading."""
    text = str(getattr(error, "orig", error))
    if "UNIQUE constraint failed" in text:
        return "That is already recorded on this coin."
    return text
