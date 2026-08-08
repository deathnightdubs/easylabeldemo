"""Regressions reported from real use.

Each test here reproduces a specific reported symptom. They are kept together so it is obvious
what was broken and stays fixed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from numis.db import create_library
from numis.ui.main_window import MASTER_VIEW, MainWindow

EDIT = Qt.ItemDataRole.EditRole
DISPLAY = Qt.ItemDataRole.DisplayRole


def _window(qapp, tmp_path):
    """A window with two subcollections of different widths, as reported."""
    library = create_library(tmp_path / "Reported.numis")
    window = MainWindow(library)
    service = window.service

    wide = service.create_subcollection("Demo Wide")
    for order, (key, label, data_type) in enumerate(
        [
            ("ruler", "Ruler", "text"),
            ("denom", "Denomination", "text"),
            ("date_issued", "Date", "date"),
            ("weight", "Weight", "weight"),
        ]
    ):
        field = service.create_field(key, label, data_type)
        service.show_field(wide, field, show_in_table=True, sort_order=order)
    for _ in range(3):
        service.add_specimen(wide)

    window.session.commit()
    window._reload_subcollections(keep="Demo Wide")
    return window, library, service, wide


class TestSortAcrossSubcollections:
    """Reported: an IndexError, and new subcollections showing the previous one's coins."""

    def test_switching_to_a_narrower_subcollection_after_sorting(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            # Sort by the last column, which only the wide subcollection has.
            last = model.columnCount() - 1
            model.sort(last, Qt.SortOrder.AscendingOrder)
            assert model.rowCount() == 3

            # A brand new subcollection has no columns at all.
            narrow = service.create_subcollection("Brand New")
            window.session.commit()
            window._reload_subcollections(keep="Brand New")

            # It must be empty, not showing the previous subcollection's coins.
            assert window.current_subcollection().id == narrow.id
            assert model.rowCount() == 0
        finally:
            window.session.close()
            library.close()

    def test_adding_rows_to_a_new_subcollection_shows_them_immediately(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            window.model.sort(window.model.columnCount() - 1, Qt.SortOrder.AscendingOrder)
            service.create_subcollection("Brand New")
            window.session.commit()
            window._reload_subcollections(keep="Brand New")

            window._add_rows(2)
            assert window.model.rowCount() == 2
        finally:
            window.session.close()
            library.close()

    def test_the_toolbar_is_enabled_after_switching(self, qapp, tmp_path):
        """Reported: add row and columns stayed greyed out on a real subcollection."""
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            window.model.sort(window.model.columnCount() - 1, Qt.SortOrder.AscendingOrder)
            service.create_subcollection("Brand New")
            window.session.commit()

            window._reload_subcollections(keep=MASTER_VIEW)
            assert not window.add_row_action.isEnabled()

            window.subcollection_combo.setCurrentText("Brand New")
            assert window.add_row_action.isEnabled()
            assert window.columns_action.isEnabled()
        finally:
            window.session.close()
            library.close()

    def test_a_sort_does_not_silently_apply_to_a_different_column(self, qapp, tmp_path):
        """Sorting is remembered by which column it was, not by its position."""
        window, library, service, wide = _window(qapp, tmp_path)
        try:
            model = window.model
            headers = [
                model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
            ]
            date_column = headers.index("Date")
            model.sort(date_column, Qt.SortOrder.AscendingOrder)

            other = service.create_subcollection("Other")
            weight = service.field_by_key("weight")
            service.show_field(other, weight, show_in_table=True)
            window.session.commit()
            window._reload_subcollections(keep="Other")

            # 'Other' has no Date column, so no sort should be in force.
            assert model.sort_key is None
        finally:
            window.session.close()
            library.close()


class TestUndoUpdatesTheGrid:
    """Reported: undo did not appear until the view was switched."""

    def test_undo_refreshes_the_grid_immediately(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            headers = [
                model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
            ]
            ruler = headers.index("Ruler")

            model.setData(model.index(0, ruler), "Victoria", EDIT)
            assert model.data(model.index(0, ruler), DISPLAY) == "Victoria"

            window.undo.undo()
            # No refresh() call, no view switch: the grid must already be correct.
            assert model.data(model.index(0, ruler), DISPLAY) == ""

            window.undo.redo()
            assert model.data(model.index(0, ruler), DISPLAY) == "Victoria"
        finally:
            window.session.close()
            library.close()

    def test_undoing_an_added_row_updates_the_row_count_immediately(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            window._add_rows(2)
            assert window.model.rowCount() == 5
            window.undo.undo()
            assert window.model.rowCount() == 3
        finally:
            window.session.close()
            library.close()


class TestEditableIdentity:
    """Reported: the name could not be edited, and specimens needed a real ID."""

    def test_every_specimen_gets_an_id_automatically(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            codes = [s.inventory_code for s in window.model._specimens]
            assert all(code for code in codes)
            assert len(set(codes)) == 3
        finally:
            window.session.close()
            library.close()

    def test_the_id_column_can_be_edited(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
            assert model.setData(model.index(0, 0), "A-100", EDIT)
            assert model.data(model.index(0, 0), DISPLAY) == "A-100"
        finally:
            window.session.close()
            library.close()

    def test_a_duplicate_id_is_refused_with_a_message(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            messages: list[str] = []
            model.error.connect(messages.append)
            existing = model.data(model.index(1, 0), DISPLAY)

            assert model.setData(model.index(0, 0), existing, EDIT) is False
            assert messages and "already" in messages[0].lower()
        finally:
            window.session.close()
            library.close()

    def test_the_name_column_can_be_edited(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            assert model.flags(model.index(0, 1)) & Qt.ItemFlag.ItemIsEditable
            assert model.setData(model.index(0, 1), "1780 Thaler", EDIT)
            assert model.data(model.index(0, 1), DISPLAY) == "1780 Thaler"

            window.undo.undo()
            assert model.data(model.index(0, 1), DISPLAY) != "1780 Thaler"
        finally:
            window.session.close()
            library.close()


class TestChangingSubcollection:
    """Requested: move a specimen to another subcollection easily."""

    def test_the_subcollection_column_shows_where_a_coin_lives(self, qapp, tmp_path):
        window, library, service, wide = _window(qapp, tmp_path)
        try:
            model = window.model
            assert model.data(model.index(0, 2), DISPLAY) == "Demo Wide"
        finally:
            window.session.close()
            library.close()

    def test_typing_a_subcollection_name_moves_the_coin(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            service.create_subcollection("Ancients")
            window.session.commit()
            window._reload_subcollections(keep="Demo Wide")
            model = window.model

            assert model.setData(model.index(0, 2), "Ancients", EDIT)
            window.subcollection_combo.setCurrentText("Ancients")
            assert window.model.rowCount() == 1

            window.subcollection_combo.setCurrentText("Demo Wide")
            assert window.model.rowCount() == 2
        finally:
            window.session.close()
            library.close()

    def test_an_unknown_subcollection_name_is_refused(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            model = window.model
            messages: list[str] = []
            model.error.connect(messages.append)
            assert model.setData(model.index(0, 2), "Nowhere", EDIT) is False
            assert messages and "Nowhere" in messages[0]
        finally:
            window.session.close()
            library.close()

    def test_moving_is_undoable(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            service.create_subcollection("Ancients")
            window.session.commit()
            window._reload_subcollections(keep="Demo Wide")
            model = window.model
            model.setData(model.index(0, 2), "Ancients", EDIT)

            window.undo.undo()
            assert model.data(model.index(0, 2), DISPLAY) == "Demo Wide"
        finally:
            window.session.close()
            library.close()

    def test_the_move_action_handles_several_rows(self, qapp, tmp_path):
        window, library, service, _ = _window(qapp, tmp_path)
        try:
            ancients = service.create_subcollection("Ancients")
            window.session.commit()
            window._reload_subcollections(keep="Demo Wide")

            window.view.selectAll()
            window.move_to_subcollection(ancients)

            window.subcollection_combo.setCurrentText("Ancients")
            assert window.model.rowCount() == 3
        finally:
            window.session.close()
            library.close()
