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
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from .. import grading
from ..constants import LINK_KINDS
from ..errors import NumisError
from ..models import (
    Catalog,
    CatalogReference,
    Certification,
    ExternalLink,
    GradeScale,
    GradingCompany,
    Specimen,
    SpecimenGrade,
)
from ..services import CollectionService
from .commands import readable
from .grade_dialog import GradeDialog

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
        self.edit_button = QPushButton("Edit…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("▲")
        self.down_button = QPushButton("▼")
        for arrow in (self.up_button, self.down_button):
            arrow.setMaximumWidth(32)
            arrow.setToolTip("Change which one a single-value column shows")
        buttons = QHBoxLayout()
        for widget in (
            self.add_button, self.edit_button, self.remove_button,
            self.up_button, self.down_button,
        ):
            buttons.addWidget(widget)
        buttons.addStretch()

        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.doubleClicked.connect(lambda _index: self.edit_button.click())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.addWidget(heading)
        layout.addWidget(self.hint)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def _context_menu(self, position: object) -> None:
        """Right-click an entry to edit, remove or promote it."""
        if self.list.currentItem() is None:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", self.edit_button.click)
        menu.addAction("Remove", self.remove_button.click)
        menu.addSeparator()
        menu.addAction("Show this one first", self.up_button.click)
        menu.exec(self.list.viewport().mapToGlobal(position))

    def selected_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def fill(self, rows: Sequence[tuple[str, int]]) -> None:
        self.list.clear()
        for label, identifier in rows:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, identifier)
            self.list.addItem(item)
        for widget in (self.edit_button, self.remove_button, self.up_button, self.down_button):
            widget.setEnabled(bool(rows))

    def set_enabled(self, enabled: bool) -> None:
        self.add_button.setEnabled(enabled)
        for widget in (self.edit_button, self.remove_button, self.up_button, self.down_button):
            widget.setEnabled(enabled and self.list.count() > 0)

    def ordered_ids(self) -> list[int]:
        return [
            self.list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list.count())
        ]


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
        self.primary = QCheckBox("Show this one first")
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
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://… the company's verification page, optional")
        self.sticker = QComboBox()
        self.sticker.addItem("(none)", None)
        for modifier in service.modifiers("sticker"):
            self.sticker.addItem(
                f"{modifier.issuer or modifier.label}", modifier.code
            )
        self.sticker_detail = QLineEdit()
        self.sticker_detail.setPlaceholderText("green, gold — what this sticker says")
        self.primary = QCheckBox("Show this one first")
        self.primary.setChecked(True)

        form = QFormLayout()
        form.addRow("Company", self.company)
        form.addRow("Certificate no.", self.number)
        form.addRow("Grade on it", self.grade)
        form.addRow("Graded on", self.graded_on)
        form.addRow("Verification link", self.url)
        form.addRow("Sticker awarded", self.sticker)
        form.addRow("Sticker says", self.sticker_detail)
        form.addRow("", self.primary)
        note = QLabel(
            "Several certifications can be current at once, so adding an endorsement does not "
            "replace a grading company's own. A sticker chosen here is recorded as issued by "
            "this certification and attached to the grade above."
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


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


class DetailPanel(QDockWidget):
    """Catalogue numbers, grades, certifications and links for the selected coin."""

    changed = Signal()
    #: Something could not be saved. The window turns this into a message.
    failed = Signal(str)

    def __init__(self, service: CollectionService, parent: QWidget | None = None) -> None:
        super().__init__("Selected coin", parent)
        self.last_error: str | None = None
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

        self.catalogues.edit_button.clicked.connect(
            lambda: self._edit(CatalogReference, self.catalogues)
        )
        self.grades.edit_button.clicked.connect(
            lambda: self._edit(SpecimenGrade, self.grades)
        )
        self.certifications.edit_button.clicked.connect(
            lambda: self._edit(Certification, self.certifications)
        )
        self.links.edit_button.clicked.connect(lambda: self._edit(ExternalLink, self.links))
        for model, section in (
            (CatalogReference, self.catalogues),
            (SpecimenGrade, self.grades),
            (Certification, self.certifications),
            (ExternalLink, self.links),
        ):
            section.up_button.clicked.connect(
                lambda _checked=False, m=model, s=section: self._move(m, s, -1)
            )
            section.down_button.clicked.connect(
                lambda _checked=False, m=model, s=section: self._move(m, s, 1)
            )

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
                    f"{reference.rank}. "
                    f"{self.service.session.get(Catalog, reference.catalog_id).code} "
                    f"{reference.number_raw}",
                    reference.id,
                )
                for reference in self.service.references_for(specimen)
            ]
        )
        self.grades.fill(
            [
                (
                    f"{grade.rank}. "
                    + grading.render(grade, grading.GradeDisplay(modifier_details=True))
                    + f"  [{grade.assigned_by or grade.source}]"
                    + f"  = {grade.normalised:g}" * (grade.normalised is not None),
                    grade.id,
                )
                for grade in self.service.grades_for(specimen)
            ]
        )
        self.certifications.fill(
            [
                (
                    f"{certification.rank}. "
                    + f"{certification.company.code} {certification.cert_number or ''}".strip()
                    + f"  · {certification.status}"
                    + ("  · link" if certification.verification_url else "")
                    + "".join(
                        f"  · {link.modifier.issuer or link.modifier.label}"
                        + (f" {link.detail}" if link.detail else "")
                        for link in self.service.stickers_for(certification)
                    ),
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

    def _edit(self, model: type, section: _Section) -> None:
        """Open the right editor for the selected entry."""
        row_id = section.selected_id()
        if row_id is None:
            return
        if model is SpecimenGrade:
            self._edit_grade(row_id)
            return
        row = self.service.session.get(model, row_id)
        if row is None:
            return
        if model is ExternalLink:
            self._edit_link(row)
        elif model is CatalogReference:
            self._edit_reference(row)
        elif model is Certification:
            self._edit_certification(row)

    def _edit_reference(self, reference: CatalogReference) -> None:
        from ..catalogs import build_reference_columns

        catalog = self.service.session.get(Catalog, reference.catalog_id)
        number, accepted = QInputDialog.getText(
            self,
            "Edit catalogue number",
            f"Number in {catalog.code}:",
            text=reference.number_raw,
        )
        if not accepted or not number.strip():
            return
        try:
            for name, value in build_reference_columns(
                number,
                catalog_code=catalog.code,
                letter_prefix_order=catalog.letter_prefix_order,
            ).items():
                setattr(reference, name, value)
            self.service.session.commit()
        except Exception as exc:
            self.service.session.rollback()
            QMessageBox.warning(self, "Catalogue number", _readable(exc))
        self.refresh()
        self.changed.emit()

    def _edit_certification(self, certification: Certification) -> None:
        number, accepted = QInputDialog.getText(
            self,
            "Edit certification",
            f"{certification.company.code} certificate number:",
            text=certification.cert_number or "",
        )
        if not accepted:
            return
        url, accepted = QInputDialog.getText(
            self,
            "Edit certification",
            "Verification link:",
            text=certification.verification_url or "",
        )
        if not accepted:
            return
        certification.cert_number = number.strip() or None
        certification.verification_url = url.strip() or None
        self.service.session.commit()
        self.refresh()
        self.changed.emit()

    def _edit_link(self, link: ExternalLink) -> None:
        dialog = LinkDialog(self)
        dialog.kind.setCurrentText(link.kind)
        dialog.url.setText(link.url)
        dialog.label.setText(link.label or "")
        dialog.reference.setText(link.reference or "")
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.url.text().strip():
            return
        link.kind = dialog.kind.currentText()
        link.url = dialog.url.text().strip()
        link.label = dialog.label.text().strip() or None
        link.reference = dialog.reference.text().strip() or None
        self.service.session.commit()
        self.refresh()
        self.changed.emit()

    def _move(self, model: type, section: _Section, delta: int) -> None:
        """Reorder an entry, which is what decides what a single-value column shows."""
        row_id = section.selected_id()
        if row_id is None:
            return
        ids = section.ordered_ids()
        position = ids.index(row_id)
        target = position + delta
        if not 0 <= target < len(ids):
            return
        ids[position], ids[target] = ids[target], ids[position]
        self.service.reorder([self.service.session.get(model, identifier) for identifier in ids])
        self.service.session.commit()
        self.refresh()
        self.changed.emit()
        section.list.setCurrentRow(target)

    def _apply(self, command: object) -> bool:
        """Run a command and report anything it could not do. Returns whether it worked.

        Failures are announced rather than shown here, so the panel does not open dialogs of
        its own — the window decides how to present them, and tests can read them.
        """
        if self.run_command is not None:
            self.run_command(command)
        self.last_error = getattr(command, "error", None)
        self.refresh()
        self.changed.emit()
        if self.last_error:
            self.failed.emit(self.last_error)
            return False
        return True

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


#: Shared with the window, which reports failures the same way.
_readable = readable
