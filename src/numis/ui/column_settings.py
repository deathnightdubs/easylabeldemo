"""Choosing what a catalogue, grade, certification or links column shows.

A coin can hold any number of each of these, and a column is one cell wide, so something has to
give. This dialog is where the user says what: everything, only one catalogue's numbers, or the
one they put first.

The preview is the point of the dialog. These settings are hard to hold in your head — "show
modifiers" and "spell out what each modifier says" are a sentence apart and look nothing alike on
a real coin — so the dialog renders an actual row from the collection as you change them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..columns import MAX_RANK, ColumnDisplay
from ..constants import GRADE_SOURCES, LINK_KINDS
from ..models import Catalog, GradingCompany, Specimen, SpecimenGrade, Subcollection
from ..services import CollectionService

#: How each system reads in the interface, and what "only from…" is asking for.
KIND_LABELS = {
    "catalogues": "catalogue numbers",
    "grades": "grades",
    "certifications": "certifications",
    "links": "links",
}

ONLY_LABELS = {
    "catalogues": "Only this catalogue",
    "grades": "Only this source or grader",
    "certifications": "Only this company",
    "links": "Only this kind of link",
}

RANK_WORDS = {1: "first", 2: "second", 3: "third"}


def _ordinal(position: int) -> str:
    return RANK_WORDS.get(position, f"{position}th")


class ColumnSettingsDialog(QDialog):
    """Per-column display settings for one special system."""

    def __init__(
        self,
        service: CollectionService,
        subcollection: Subcollection,
        kind: str,
        display: ColumnDisplay,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.subcollection = subcollection
        self.kind = kind
        self.setWindowTitle(f"{KIND_LABELS.get(kind, kind).capitalize()} column")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.addWidget(self._which_group(display))
        extras = self._extras_group(display)
        if extras is not None:
            layout.addWidget(extras)

        self.separator = QLineEdit(display.separator)
        self.separator.setMaximumWidth(90)
        separator_row = QFormLayout()
        separator_row.addRow("Separator", self.separator)
        layout.addLayout(separator_row)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setStyleSheet(
            "padding: 6px; border: 1px solid #ccc; background: #fbfbfb; font-family: monospace;"
        )
        preview_box = QVBoxLayout()
        preview_box.addWidget(QLabel("This column would read:"))
        preview_box.addWidget(self.preview)
        layout.addLayout(preview_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for widget in self._inputs():
            if isinstance(widget, QCheckBox | QRadioButton):
                widget.toggled.connect(self._update)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._update)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._update)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._update)

        self._update()

    # -- construction -----------------------------------------------------

    def _which_group(self, display: ColumnDisplay) -> QGroupBox:
        box = QGroupBox(f"Which {KIND_LABELS.get(self.kind, 'entries')} to show")
        self.mode_all = QRadioButton("All of them")
        self.mode_only = QRadioButton(ONLY_LABELS.get(self.kind, "Only this one"))
        self.mode_rank = QRadioButton("Only the one I ranked")

        self.modes = QButtonGroup(self)
        for button in (self.mode_all, self.mode_only, self.mode_rank):
            self.modes.addButton(button)

        self.only = QComboBox()
        self.only.setEditable(True)
        self.only.addItems(self._only_choices())
        if display.only:
            self.only.setCurrentText(display.only)
        elif self.only.count():
            self.only.setCurrentIndex(0)

        self.rank = QSpinBox()
        self.rank.setRange(1, MAX_RANK)
        self.rank.setValue(display.rank)
        self.rank.setToolTip(
            "Precedence is set in the coin's own details, with the up and down arrows next to "
            "the list. 1 is whichever one you put at the top."
        )

        only_row = QHBoxLayout()
        only_row.addWidget(self.mode_only)
        only_row.addWidget(self.only, 1)
        rank_row = QHBoxLayout()
        rank_row.addWidget(self.mode_rank)
        rank_row.addWidget(self.rank)
        rank_row.addStretch()

        inner = QVBoxLayout(box)
        inner.addWidget(self.mode_all)
        inner.addLayout(only_row)
        inner.addLayout(rank_row)

        {"only": self.mode_only, "rank": self.mode_rank}.get(
            display.mode, self.mode_all
        ).setChecked(True)
        return box

    def _extras_group(self, display: ColumnDisplay) -> QGroupBox | None:
        """The part that differs by system: a grade has modifiers, a link has a label."""
        self.show_modifiers = QCheckBox("Modifiers")
        self.modifier_details = QCheckBox("Spell out what each modifier says")
        self.show_scale = QCheckBox("Scale")
        self.show_source = QCheckBox("Source")
        self.show_assigned_by = QCheckBox("Who assigned it")
        self.show_catalogue = QCheckBox("The catalogue's name")
        self.show_labels = QCheckBox("List the links instead of counting them")

        for widget, value in (
            (self.show_modifiers, display.show_modifiers),
            (self.modifier_details, display.modifier_details),
            (self.show_scale, display.show_scale),
            (self.show_source, display.show_source),
            (self.show_assigned_by, display.show_assigned_by),
            (self.show_catalogue, display.show_catalogue),
            (self.show_labels, display.show_labels),
        ):
            widget.setChecked(bool(value))

        self.modifier_details.setToolTip("CAC Gold rather than CAC; Details — Harshly Cleaned.")
        self.show_assigned_by.setToolTip(
            "Individual grades can opt out of this in their own settings, so recording that a "
            "dealer graded a hundred coins need not put their name on a hundred rows."
        )
        self.show_catalogue.setToolTip("H 1.01 rather than 1.01.")

        if self.kind == "grades":
            box = QGroupBox("How much of each grade")
            inner = QVBoxLayout(box)
            inner.addWidget(self.show_modifiers)
            indented = QHBoxLayout()
            indented.addSpacing(22)
            indented.addWidget(self.modifier_details)
            inner.addLayout(indented)
            inner.addWidget(self.show_scale)
            inner.addWidget(self.show_source)
            inner.addWidget(self.show_assigned_by)
            self.show_modifiers.toggled.connect(self.modifier_details.setEnabled)
            self.modifier_details.setEnabled(self.show_modifiers.isChecked())
            return box

        if self.kind == "catalogues":
            box = QGroupBox("How much of each number")
            QVBoxLayout(box).addWidget(self.show_catalogue)
            return box

        if self.kind == "links":
            box = QGroupBox("How to show them")
            QVBoxLayout(box).addWidget(self.show_labels)
            return box

        return None

    def _only_choices(self) -> list[str]:
        """What there is to filter by, taken from the collection rather than invented."""
        session = self.service.session
        if self.kind == "catalogues":
            return [
                catalog.code
                for catalog in session.scalars(
                    select(Catalog).where(Catalog.is_archived == 0).order_by(Catalog.code)
                )
            ]
        if self.kind == "certifications":
            return [
                company.code
                for company in session.scalars(
                    select(GradingCompany)
                    .where(GradingCompany.is_archived == 0)
                    .order_by(GradingCompany.code)
                )
            ]
        if self.kind == "links":
            return list(LINK_KINDS)
        # Grades are not owned by a company, so offer both the sources and the names actually
        # used on this collection's grades.
        graders = sorted(
            {
                name.strip()
                for name in session.scalars(
                    select(SpecimenGrade.assigned_by).where(
                        SpecimenGrade.assigned_by.is_not(None)
                    )
                )
                if name and name.strip()
            }
        )
        return [*graders, *GRADE_SOURCES]

    def _inputs(self) -> list[QWidget]:
        return [
            self.mode_all,
            self.mode_only,
            self.mode_rank,
            self.only,
            self.rank,
            self.separator,
            self.show_modifiers,
            self.modifier_details,
            self.show_scale,
            self.show_source,
            self.show_assigned_by,
            self.show_catalogue,
            self.show_labels,
        ]

    # -- reactions --------------------------------------------------------

    def _update(self) -> None:
        self.only.setEnabled(self.mode_only.isChecked())
        self.rank.setEnabled(self.mode_rank.isChecked())
        self.separator.setEnabled(not self.mode_rank.isChecked())
        if self.mode_rank.isChecked():
            self.mode_rank.setText(f"Only the one I ranked {_ordinal(self.rank.value())}")
        else:
            self.mode_rank.setText("Only the one I ranked")
        self.preview.setText(self._preview_text())

    def _preview_text(self) -> str:
        """Render a real row, so the settings are judged on the collection they apply to."""
        display = self.display()
        specimen = self._example()
        if specimen is None:
            return "(nothing in this subcollection to preview yet)"
        rendered = self.service.special_cell(specimen, self.kind, display)
        name = specimen.display_name or specimen.inventory_code or "a coin"
        if not rendered:
            return f"(blank for {name} — nothing here matches these settings)"
        return f"{name}:  {rendered}"

    def _example(self) -> Specimen | None:
        """The first coin in this subcollection that has anything to show, else the first."""
        specimens = list(
            self.service.session.scalars(
                select(Specimen)
                .where(
                    Specimen.subcollection_id == self.subcollection.id,
                    Specimen.deleted_at.is_(None),
                )
                .order_by(Specimen.id)
                .limit(60)
            )
        )
        for specimen in specimens:
            if self.service.special_cell(specimen, self.kind, ColumnDisplay()):
                return specimen
        return specimens[0] if specimens else None

    # -- result -----------------------------------------------------------

    def display(self) -> ColumnDisplay:
        """The settings as chosen. Safe to call whether or not the dialog was accepted."""
        if self.mode_only.isChecked():
            mode = "only"
        elif self.mode_rank.isChecked():
            mode = "rank"
        else:
            mode = "all"
        return ColumnDisplay(
            mode=mode,
            only=self.only.currentText().strip() or None,
            rank=self.rank.value(),
            separator=self.separator.text() or " · ",
            show_modifiers=self.show_modifiers.isChecked(),
            modifier_details=self.modifier_details.isChecked(),
            show_scale=self.show_scale.isChecked(),
            show_source=self.show_source.isChecked(),
            show_assigned_by=self.show_assigned_by.isChecked(),
            show_catalogue=self.show_catalogue.isChecked(),
            show_labels=self.show_labels.isChecked(),
        )
