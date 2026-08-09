"""The grade window, and the modifier list inside it.

The reported fault: the "What it says" column could not be typed into, which made the whole
per-coin detail mechanism — and therefore the column option that shows it — look like it did
nothing at all.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog

from numis import grading
from numis.db import create_library
from numis.ui.grade_dialog import GradeDialog
from numis.ui.main_window import MainWindow
from numis.ui.modifier_dialogs import ManageModifiersDialog

CHECKED = Qt.CheckState.Checked
CODE = Qt.ItemDataRole.UserRole


@pytest.fixture
def window(qapp, tmp_path):
    library = create_library(tmp_path / "Grades.numis")
    win = MainWindow(library)
    service = win.service
    modern = service.create_subcollection("Modern")
    service.add_specimen(modern, display_name="Test coin")
    service.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")
    service.create_grade_modifier("DETAILS", "Details", "detail", -0.4)
    service.create_grade_modifier("FB", "Full Bands", "strike", 0.15, abbreviation="FB")
    service.create_grade_modifier("RD", "Red", "colour", 0.0, abbreviation="RD")
    service.create_grade_modifier(
        "CACG", "CAC Gold", "sticker", 0.15, abbreviation="CAC Gold", issuer="CAC"
    )
    win.session.commit()
    win._reload_subcollections(keep="Modern")
    win.view.selectRow(0)
    yield win
    win.session.close()
    library.close()


def row_of(dialog: GradeDialog, code: str) -> int:
    table = dialog.modifiers
    for row in range(table.rowCount()):
        if table.item(row, 0).data(CODE) == code:
            return row
    raise AssertionError(f"no row for {code!r}")


def tick(dialog: GradeDialog, code: str, says: str | None = None) -> None:
    """Do what a user does: tick the modifier and type what it says on this coin."""
    row = row_of(dialog, code)
    dialog.modifiers.item(row, 0).setCheckState(CHECKED)
    if says is not None:
        dialog.modifiers.item(row, 1).setText(says)


class TestTypingWhatAModifierSays:
    def test_the_cell_can_be_edited(self, window):
        """It could not: NoSelection had also disabled editing."""
        dialog = GradeDialog(window.service, None, window)
        table = dialog.modifiers
        assert table.selectionMode() != QAbstractItemView.SelectionMode.NoSelection

        cell = table.item(0, 1)
        assert cell.flags() & Qt.ItemFlag.ItemIsEditable

    def test_an_editor_actually_opens_on_it(self, window):
        dialog = GradeDialog(window.service, None, window)
        table = dialog.modifiers
        index = table.model().index(row_of(dialog, "DETAILS"), 1)
        table.setCurrentIndex(index)
        table.edit(index)
        assert table.state() == QAbstractItemView.State.EditingState

    def test_what_is_typed_is_collected(self, window):
        dialog = GradeDialog(window.service, None, window)
        tick(dialog, "DETAILS", "Harshly Cleaned")
        assert dialog.checked_modifiers() == {"DETAILS": "Harshly Cleaned"}
        assert dialog.modifier_pairs() == [("DETAILS", "Harshly Cleaned")]

    def test_a_modifier_can_be_ticked_without_saying_anything(self, window):
        dialog = GradeDialog(window.service, None, window)
        tick(dialog, "FB")
        assert dialog.modifier_pairs() == [("FB", None)]

    def test_it_reaches_the_saved_grade(self, window):
        service, detail = window.service, window.detail
        dialog = GradeDialog(service, None, window)
        dialog.label.setText("MS63")
        dialog.base_value.setValue(63.0)
        tick(dialog, "DETAILS", "Harshly Cleaned")

        grade = service.add_grade(
            detail.specimen,
            dialog.resolve_scale(),
            **dialog.values(),
        )
        window.session.commit()
        assert [link.detail for link in grade.modifier_links] == ["Harshly Cleaned"]
        assert grading.render(
            grade, grading.GradeDisplay(modifier_details=True)
        ) == "MS63 Details — Harshly Cleaned"

    def test_editing_a_grade_shows_what_it_already_says(self, window):
        service, detail = window.service, window.detail
        grade = service.add_grade(
            detail.specimen, None, "MS63", base_value=63.0,
            modifiers=[("DETAILS", "Harshly Cleaned")],
        )
        window.session.commit()

        dialog = GradeDialog(service, grade, window)
        row = row_of(dialog, "DETAILS")
        assert dialog.modifiers.item(row, 0).checkState() == CHECKED
        assert dialog.modifiers.item(row, 1).text() == "Harshly Cleaned"


class TestTheModifierList:
    def test_the_coin_s_own_modifiers_come_first(self, window):
        service, detail = window.service, window.detail
        grade = service.add_grade(
            detail.specimen, None, "MS63", base_value=63.0, modifiers=[("CACG", None)]
        )
        window.session.commit()

        dialog = GradeDialog(service, grade, window)
        assert dialog.modifiers.item(0, 0).data(CODE) == "CACG"

    def test_they_are_listed_in_the_order_they_read(self, window):
        service, detail = window.service, window.detail
        grade = service.add_grade(
            detail.specimen, None, "MS63", base_value=63.0,
            modifiers=[("DETAILS", None), ("FB", None), ("RD", None)],
        )
        window.session.commit()

        dialog = GradeDialog(service, grade, window)
        listed = [dialog.modifiers.item(row, 0).data(CODE) for row in range(3)]
        assert listed == ["FB", "RD", "DETAILS"]

    def test_a_row_names_both_the_short_form_and_the_full_name(self, window):
        dialog = GradeDialog(window.service, None, window)
        text = dialog.modifiers.item(row_of(dialog, "FB"), 0).text()
        assert "FB" in text
        assert "Full Bands" in text

    def test_a_modifier_with_no_short_form_is_named_once(self, window):
        dialog = GradeDialog(window.service, None, window)
        text = dialog.modifiers.item(row_of(dialog, "DETAILS"), 0).text()
        assert text.count("Details") == 1


class TestThePreview:
    def test_it_shows_the_readings_a_column_can_choose_between(self, window):
        """Showing only one is how "spell out what each one says" looked like a no-op."""
        dialog = GradeDialog(window.service, None, window)
        dialog.label.setText("MS")
        tick(dialog, "DETAILS", "Harshly Cleaned")
        tick(dialog, "FB")

        readings = dialog.preview.text().splitlines()
        assert "MS FB Details" in readings
        assert "MS FB Details — Harshly Cleaned" in readings
        assert "MS Full Bands Details — Harshly Cleaned" in readings

    def test_identical_readings_are_not_repeated(self, window):
        dialog = GradeDialog(window.service, None, window)
        dialog.label.setText("MS63")
        readings = dialog.preview.text().splitlines()
        assert readings == ["MS63"]

    def test_a_sticker_previews_by_its_own_name(self, window):
        dialog = GradeDialog(window.service, None, window)
        dialog.label.setText("MS63")
        tick(dialog, "CACG")
        assert dialog.preview.text().splitlines()[0] == "MS63 CAC Gold"

    def test_the_preview_agrees_with_what_gets_saved(self, window):
        service, detail = window.service, window.detail
        dialog = GradeDialog(service, None, window)
        dialog.label.setText("MS63")
        dialog.base_value.setValue(63.0)
        tick(dialog, "DETAILS", "Cleaned")
        tick(dialog, "RD")
        previewed = dialog.preview.text().splitlines()[0]

        grade = service.add_grade(detail.specimen, None, **dialog.values())
        window.session.commit()
        assert grading.render(grade) == previewed

    def test_the_calculated_value_is_shown(self, window):
        dialog = GradeDialog(window.service, None, window)
        dialog.label.setText("MS63")
        dialog.base_value.setValue(63.0)
        tick(dialog, "DETAILS")
        assert dialog.calculated.text() == "62.6"


class TestReorderingFromTheManagementWindow:
    def test_it_lists_them_in_reading_order(self, window):
        dialog = ManageModifiersDialog(window.service, window)
        listed = [
            dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())
        ]
        assert listed == ["Full Bands", "Red", "CAC Gold", "Details"]

    def test_moving_one_up_changes_how_grades_read(self, window):
        service, detail = window.service, window.detail
        grade = service.add_grade(
            detail.specimen, None, "MS", base_value=60.0,
            modifiers=[("DETAILS", None), ("FB", None), ("RD", None)],
        )
        window.session.commit()
        assert grading.render(grade) == "MS FB RD Details"

        dialog = ManageModifiersDialog(service, window)
        dialog.table.selectRow(3)  # Details, last
        for _ in range(3):
            dialog._move(-1)

        assert grading.render(grade) == "MS Details FB RD"

    def test_moving_one_down_works_too(self, window):
        service = window.service
        dialog = ManageModifiersDialog(service, window)
        dialog.table.selectRow(0)  # Full Bands, first
        dialog._move(1)
        assert [m.code for m in service.modifiers_in_reading_order()][:2] == ["RD", "FB"]

    def test_the_selection_follows_the_row(self, window):
        dialog = ManageModifiersDialog(window.service, window)
        dialog.table.selectRow(0)
        dialog._move(1)
        assert dialog.table.item(dialog.table.currentRow(), 0).text() == "Full Bands"

    def test_moving_past_the_end_does_nothing(self, window):
        service = window.service
        dialog = ManageModifiersDialog(service, window)
        before = [m.code for m in service.modifiers_in_reading_order()]
        dialog.table.selectRow(0)
        dialog._move(-1)
        dialog.table.selectRow(dialog.table.rowCount() - 1)
        dialog._move(1)
        assert [m.code for m in service.modifiers_in_reading_order()] == before

    def test_moving_with_nothing_selected_does_nothing(self, window):
        service = window.service
        dialog = ManageModifiersDialog(service, window)
        dialog.table.clearSelection()
        dialog.table.setCurrentCell(-1, -1)
        before = [m.code for m in service.modifiers_in_reading_order()]
        dialog._move(-1)
        assert [m.code for m in service.modifiers_in_reading_order()] == before

    def test_the_order_survives_reopening_the_library(self, window, tmp_path):
        service = window.service
        order = service.modifiers_in_reading_order()
        service.reorder_modifiers([order[3], order[0], order[1], order[2]])
        window.session.commit()
        path = window.library.path
        window.session.close()
        window.library.close()

        from numis.db import open_library

        library = open_library(path)
        reopened = MainWindow(library)
        try:
            codes = [m.code for m in reopened.service.modifiers_in_reading_order()]
            assert codes == ["DETAILS", "FB", "RD", "CACG"]
        finally:
            reopened.session.close()
            library.close()


class TestTheColumnOptionsNowDoSomething:
    """The reported symptom: the option existed and had no visible effect."""

    def _cell(self, window, **settings) -> str:
        from numis.columns import ColumnDisplay

        service = window.service
        subcollection = window.current_subcollection()
        block = service.block_for(subcollection, "grades")
        if block is None:
            block = service.show_special_block(
                subcollection, "grades", display_label="Grade", show_in_table=True
            )
        service.set_block_display(block, ColumnDisplay(**settings))
        window.session.commit()
        window.model.refresh()
        model = window.model
        section = next(
            i
            for i in range(model.columnCount())
            if model.headerData(i, Qt.Orientation.Horizontal) == "Grade"
        )
        return model.data(model.index(0, section), Qt.ItemDataRole.DisplayRole)

    @pytest.fixture(autouse=True)
    def _graded(self, window):
        service = window.service
        service.add_grade(
            window.detail.specimen, None, "MS", base_value=60.0,
            modifiers=[("DETAILS", "Harshly Cleaned"), ("FB", None)],
        )
        window.session.commit()

    def test_short_forms_by_default(self, window):
        assert self._cell(window) == "MS FB Details"

    def test_saying_what_it_says(self, window):
        assert self._cell(window, modifier_details=True) == "MS FB Details — Harshly Cleaned"

    def test_full_names(self, window):
        assert self._cell(window, modifier_full_names=True) == "MS Full Bands Details"

    def test_both_together(self, window):
        assert (
            self._cell(window, modifier_details=True, modifier_full_names=True)
            == "MS Full Bands Details — Harshly Cleaned"
        )

    def test_the_settings_survive_being_saved_and_read_back(self, window):
        from numis.columns import ColumnDisplay

        self._cell(window, modifier_details=True, modifier_full_names=True, sticker_issuer=True)
        block = window.service.block_for(window.current_subcollection(), "grades")
        stored = window.service.block_display(block)
        assert stored.modifier_details is True
        assert stored.modifier_full_names is True
        assert stored.sticker_issuer is True
        assert stored == ColumnDisplay(
            modifier_details=True, modifier_full_names=True, sticker_issuer=True
        )


class TestTheDialogStillWorksEndToEnd:
    def test_a_grade_can_be_added_from_the_panel(self, window, monkeypatch):
        from numis.ui import detail_panel as panel_module

        class Filled(GradeDialog):
            def __init__(self, svc, grade=None, parent=None):
                super().__init__(svc, grade, parent)
                self.label.setText("MS63")
                self.base_value.setValue(63.0)
                tick(self, "CACG", "Gold")

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(panel_module, "GradeDialog", Filled)
        window.detail.failed.disconnect()
        window.detail.grades.add_button.click()

        grade = window.service.grades_for(window.detail.specimen)[0]
        assert grade.grade_label == "MS63"
        assert [link.detail for link in grade.modifier_links] == ["Gold"]
        assert grading.render(grade) == "MS63 CAC Gold"
