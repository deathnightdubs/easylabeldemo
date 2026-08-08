"""The window itself.

Mostly guarding wiring mistakes that are invisible until the window is actually constructed:
both regressions below were real, and neither would be caught by testing the model alone.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from numis.db import create_library
from numis.services import CollectionService
from numis.ui.main_window import MASTER_VIEW, MainWindow

#: The fixture shows two fields after the three identity columns (ID, Name, Subcollection).
HEAD_COLUMN, DATE_COLUMN = 3, 4


@pytest.fixture
def window(qapp, tmp_path):
    library = create_library(tmp_path / "Win.numis")
    win = MainWindow(library)

    service = win.service
    modern = service.create_subcollection("Modern")
    ancients = service.create_subcollection("Ancients")
    head = service.create_field("head", "Head of state", "text")
    date = service.create_field("date_issued", "Date", "date")
    service.show_field(modern, head, display_label="Ruler", show_in_table=True)
    service.show_field(ancients, head, display_label="Emperor", show_in_table=True)
    for subcollection in (modern, ancients):
        service.show_field(subcollection, date, show_in_table=True, sort_order=1)
    win.session.commit()
    win._reload_subcollections(keep=MASTER_VIEW)

    yield win
    win.session.close()
    library.close()


def test_the_window_builds_without_error(window):
    """Regression: the status bar was updated before its widgets existed."""
    assert window.model.columnCount() > 0
    assert "coin(s)" in window.count_label.text()


def test_a_transient_message_does_not_wipe_out_the_row_count(window):
    """Regression: the count lived in the transient message area and disappeared."""
    window.subcollection_combo.setCurrentText("Modern")
    window._add_rows(2)
    before = window.count_label.text()
    window.statusBar().showMessage("Copied 6 cells", 4000)
    assert window.count_label.text() == before
    assert "2 coin(s)" in before


def test_the_search_box_is_connected(window):
    """Regression: the search box referenced a handler that did not exist."""
    window.search_box.setText("nothing-matches-this")
    window.search_box.returnPressed.emit()
    assert window.model.rowCount() == 0
    window.search_box.clear()
    window.search_box.returnPressed.emit()


def test_switching_subcollection_changes_the_labels(window):
    def headers() -> list[str]:
        return [
            window.model.headerData(index, Qt.Orientation.Horizontal)
            for index in range(window.model.columnCount())
        ]

    window.subcollection_combo.setCurrentText("Modern")
    assert "Ruler" in headers()

    window.subcollection_combo.setCurrentText("Ancients")
    assert "Emperor" in headers()

    window.subcollection_combo.setCurrentText(MASTER_VIEW)
    assert "Head of state" in headers()


def test_adding_columns_is_disabled_in_the_master_view(window):
    """The master view spans subcollections, so there is no single place to add to."""
    window.subcollection_combo.setCurrentText(MASTER_VIEW)
    assert not window.add_row_action.isEnabled()
    assert not window.columns_action.isEnabled()

    window.subcollection_combo.setCurrentText("Modern")
    assert window.add_row_action.isEnabled()
    assert window.columns_action.isEnabled()


def test_adding_and_deleting_rows_through_the_window(window):
    window.subcollection_combo.setCurrentText("Modern")
    window._add_rows(3)
    assert window.model.rowCount() == 3

    window.view.selectRow(0)
    window._delete_rows()
    assert window.model.rowCount() == 2
    assert "Trash" in window.statusBar().currentMessage()

    window.undo.undo()
    window.model.refresh()
    assert window.model.rowCount() == 3


def test_the_review_counter_appears_and_clears(window):
    window.subcollection_combo.setCurrentText("Modern")
    window._add_rows(1)
    window.model.setData(
        window.model.index(0, DATE_COLUMN), "1736-1795", Qt.ItemDataRole.EditRole
    )
    window._update_status()
    assert "confirm" in window.review_label.text()

    field = window.model.field_at(DATE_COLUMN)
    specimen = window.model.specimen_at(0)
    window.service.set_sort_value(specimen, field, 1765)
    window.session.commit()
    window.model.refresh()
    assert window.review_label.text() == ""


def test_go_to_next_unconfirmed_selects_the_cell(window):
    window.subcollection_combo.setCurrentText("Modern")
    window._add_rows(2)
    window.model.setData(window.model.index(1, DATE_COLUMN), "1736-1795", Qt.ItemDataRole.EditRole)
    window._go_to_review()
    assert window.view.currentIndex().row() == 1
    assert window.model.is_flagged(window.view.currentIndex())


def test_backup_writes_a_file(window):
    window._backup()
    assert "Backed up" in window.statusBar().currentMessage()
    assert list(window.library.backups_path.glob("*.db"))


def test_a_new_subcollection_appears_in_the_selector(window):
    service: CollectionService = window.service
    service.create_subcollection("Tokens")
    window.session.commit()
    window._reload_subcollections(keep="Tokens")
    assert window.subcollection_combo.currentText() == "Tokens"
