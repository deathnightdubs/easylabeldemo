"""Filtering, multi-column sorting and saved views, driven through the window."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from numis.db import create_library
from numis.filters import Criterion, FilterGroup, SortKey
from numis.ui import main_window as window_module
from numis.ui.filter_dialog import FilterDialog
from numis.ui.main_window import MainWindow

DISPLAY = Qt.ItemDataRole.DisplayRole
ACCEPTED = QDialog.DialogCode.Accepted
ID_COL, NAME_COL, SUB_COL, STATUS_COL = 0, 1, 2, 3
COUNTRY_COL, WEIGHT_COL = 4, 5


@pytest.fixture
def window(qapp, tmp_path):
    library = create_library(tmp_path / "Filters.numis")
    win = MainWindow(library)
    service = win.service
    modern = service.create_subcollection("Modern")
    for order, (key, label, data_type) in enumerate(
        [("country", "Country", "text"), ("weight", "Weight", "weight")]
    ):
        field = service.create_field(key, label, data_type)
        service.show_field(modern, field, show_in_table=True, sort_order=order)

    for name, country, weight in (
        ("Cash", "China", "4.2 g"),
        ("Penny", "Britain", "9.4 g"),
        ("Sen", "Japan", "3.1 g"),
        ("Thaler", "Austria", "28.0 g"),
    ):
        service.add_specimen(
            modern, display_name=name, values={"country": country, "weight": weight}
        )
    win.session.commit()
    win._reload_subcollections(keep="Modern")
    yield win
    win.session.close()
    library.close()


def names(window) -> list[str]:
    model = window.model
    return [model.data(model.index(row, NAME_COL), DISPLAY) for row in range(model.rowCount())]


def accept_filter(monkeypatch, group: FilterGroup) -> None:
    """Stand in for the user building a filter and pressing OK."""

    class Built(FilterDialog):
        def exec(self):
            return ACCEPTED

        def group(self):
            return group

    monkeypatch.setattr(window_module, "FilterDialog", Built)


class TestFilteringTheGrid:
    def test_a_filter_narrows_the_rows(self, window, monkeypatch):
        assert len(names(window)) == 4
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("China",)))
        )
        window._edit_filter()
        assert names(window) == ["Cash"]

    def test_the_status_bar_says_what_is_being_hidden(self, window, monkeypatch):
        """A filtered grid otherwise looks like data loss."""
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("China",)))
        )
        window._edit_filter()
        assert "Filtered" in window.filter_label.text()
        assert "Country is China" in window.filter_label.text()

    def test_the_action_shows_how_many_tests_are_active(self, window, monkeypatch):
        accept_filter(
            monkeypatch,
            FilterGroup.of(
                Criterion("field:country", "is", ("China",)),
                Criterion("field:weight", "gte", ("4",)),
            ),
        )
        window._edit_filter()
        assert window.filter_action.text() == "Filter (2)…"

    def test_clearing_brings_everything_back(self, window, monkeypatch):
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("China",)))
        )
        window._edit_filter()
        window._clear_filter()
        assert len(names(window)) == 4
        assert window.filter_label.text() == ""
        assert window.filter_action.text() == "Filter…"

    def test_clearing_when_nothing_is_filtered_says_so(self, window):
        window._clear_filter()
        assert "Nothing is filtered" in window.statusBar().currentMessage()

    def test_cancelling_leaves_the_grid_alone(self, window, monkeypatch):
        class Cancelled(FilterDialog):
            def exec(self):
                return QDialog.DialogCode.Rejected

            def group(self):
                return FilterGroup.of(Criterion("field:country", "is", ("China",)))

        monkeypatch.setattr(window_module, "FilterDialog", Cancelled)
        window._edit_filter()
        assert len(names(window)) == 4

    def test_a_filter_that_cannot_be_carried_out_does_not_empty_the_grid(
        self, window, monkeypatch
    ):
        """A broken filter must report itself, not look like a lost collection."""
        reported: list[str] = []
        window.model.error.connect(reported.append)
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:weight", "gte", ("heavy",)))
        )
        window._edit_filter()

        assert reported and "not a number" in reported[0]
        assert len(names(window)) == 4

    def test_a_filter_survives_switching_subcollection_and_back(self, window, monkeypatch):
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("China",)))
        )
        window._edit_filter()
        window._reload_subcollections(keep=window_module.MASTER_VIEW)
        window._reload_subcollections(keep="Modern")
        assert names(window) == ["Cash"]


class TestFilteringAndSearchingTogether:
    def test_a_search_is_narrowed_by_the_filter(self, window, monkeypatch):
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("China",)))
        )
        window._edit_filter()
        window.search_box.setText("Penny")
        window._search()
        assert names(window) == []

    def test_a_search_keeps_the_sort_order(self, window):
        """Searching used to discard the sort silently."""
        window.model.set_sort_keys([SortKey("field:weight")])
        window.search_box.setText("e")
        window._search()
        found = names(window)
        assert found == sorted(
            found, key=lambda name: {"Sen": 3.1, "Penny": 9.4, "Thaler": 28.0}[name]
        )


class TestSortingFromTheHeader:
    def test_clicking_sorts_by_that_column(self, window):
        window.view._header_clicked(COUNTRY_COL)
        assert names(window) == ["Thaler", "Penny", "Cash", "Sen"]

    def test_clicking_again_reverses_it(self, window):
        window.view._header_clicked(COUNTRY_COL)
        window.view._header_clicked(COUNTRY_COL)
        assert names(window) == ["Sen", "Cash", "Penny", "Thaler"]

    def test_clicking_another_column_replaces_the_sort(self, window):
        window.view._header_clicked(COUNTRY_COL)
        window.view._header_clicked(WEIGHT_COL)
        assert [key.target for key in window.model.sort_keys] == ["field:weight"]
        assert names(window) == ["Sen", "Cash", "Penny", "Thaler"]

    def test_ctrl_clicking_adds_a_tie_breaker(self, window, monkeypatch):
        """Requested: sort by one column, then by another within it."""
        service, model = window.service, window.model
        for name, country, weight in (("Yen", "Japan", "26.0 g"), ("Rin", "Japan", "0.9 g")):
            service.add_specimen(
                window.current_subcollection(),
                display_name=name,
                values={"country": country, "weight": weight},
            )
        window.session.commit()
        model.refresh()

        window.view._header_clicked(COUNTRY_COL)

        # Hold Ctrl for the second click, which is what turns "sort by" into "then by".
        from numis.ui import sheet_view as sheet_view_module

        monkeypatch.setattr(
            sheet_view_module.QGuiApplication,
            "keyboardModifiers",
            staticmethod(lambda: Qt.KeyboardModifier.ControlModifier),
        )
        window.view._header_clicked(WEIGHT_COL)

        assert [key.target for key in model.sort_keys] == ["field:country", "field:weight"]
        japanese = [name for name in names(window) if name in ("Rin", "Sen", "Yen")]
        assert japanese == ["Rin", "Sen", "Yen"]

    def test_the_indicator_follows_the_leading_column(self, window):
        window.view._header_clicked(WEIGHT_COL)
        header = window.view.horizontalHeader()
        assert header.sortIndicatorSection() == WEIGHT_COL
        assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    def test_clearing_the_sort_returns_to_creation_order(self, window):
        window.view._header_clicked(COUNTRY_COL)
        window._clear_sort()
        assert names(window) == ["Cash", "Penny", "Sen", "Thaler"]
        assert window.model.sort_keys == ()

    def test_a_sort_is_described_in_words(self, window):
        window.view._header_clicked(COUNTRY_COL)
        window.model.sort(WEIGHT_COL, Qt.SortOrder.DescendingOrder, additional=True)
        assert window.model.sort_summary() == "Country ascending, then Weight descending"

    def test_the_identity_columns_sort_too(self, window):
        window.view._header_clicked(NAME_COL)
        assert names(window) == ["Cash", "Penny", "Sen", "Thaler"]

    def test_a_sort_is_forgotten_when_its_column_goes_away(self, window):
        window.view._header_clicked(COUNTRY_COL)
        cleared: list[bool] = []
        window.model.sort_cleared.connect(lambda: cleared.append(True))

        field = window.service.field_by_key("country")
        window.service.hide_field(window.current_subcollection(), field)
        window.session.commit()
        window.model.refresh()

        assert cleared == [True]
        assert window.model.sort_keys == ()


class TestSavedViews:
    def _save(self, window, monkeypatch, name: str) -> None:
        monkeypatch.setattr(
            window_module.QInputDialog, "getText", lambda *a, **k: (name, True)
        )
        window._save_view()

    def test_a_view_saves_the_filter_and_the_sort(self, window, monkeypatch):
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("Japan",)))
        )
        window._edit_filter()
        window.model.set_sort_keys([SortKey("field:weight", descending=True)])
        self._save(window, monkeypatch, "Japanese by weight")

        view = window.service.view_by_name("Japanese by weight")
        assert view is not None
        assert window.service.view_sort(view) == (SortKey("field:weight", descending=True),)

    def test_opening_a_view_restores_what_it_described(self, window, monkeypatch):
        accept_filter(
            monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("Japan",)))
        )
        window._edit_filter()
        self._save(window, monkeypatch, "Japanese")
        window._clear_filter()
        assert len(names(window)) == 4

        window._apply_view(window.service.view_by_name("Japanese"))
        assert names(window) == ["Sen"]
        assert "Filtered" in window.filter_label.text()

    def test_saving_with_nothing_set_says_so(self, window, monkeypatch):
        window._save_view()
        assert "nothing to save" in window.statusBar().currentMessage()

    def test_a_view_appears_in_the_menu(self, window, monkeypatch):
        accept_filter(monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("Japan",))))
        window._edit_filter()
        self._save(window, monkeypatch, "Japanese")

        labels = [action.text() for action in window.views_menu.actions()]
        assert "Japanese" in labels
        assert "Save this view…" in labels

    def test_a_view_can_be_deleted(self, window, monkeypatch):
        accept_filter(monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("Japan",))))
        window._edit_filter()
        self._save(window, monkeypatch, "Japanese")

        window._delete_view(window.service.view_by_name("Japanese"))
        assert window.service.views() == []
        assert "Japanese" not in [action.text() for action in window.views_menu.actions()]

    def test_a_view_survives_reopening_the_library(self, window, monkeypatch):
        accept_filter(monkeypatch, FilterGroup.of(Criterion("field:country", "is", ("Japan",))))
        window._edit_filter()
        self._save(window, monkeypatch, "Japanese")
        path = window.library.path
        window.session.close()
        window.library.close()

        from numis.db import open_library

        library = open_library(path)
        reopened = MainWindow(library)
        try:
            reopened._reload_subcollections(keep="Modern")
            view = reopened.service.view_by_name("Japanese")
            assert view is not None
            reopened._apply_view(view)
            assert names(reopened) == ["Sen"]
        finally:
            reopened.session.close()
            library.close()


class TestTheFilterDialog:
    def _dialog(self, window, current: FilterGroup | None = None) -> FilterDialog:
        return FilterDialog(
            window.service,
            window.current_subcollection(),
            window.model.column_labels(),
            current or FilterGroup(),
            window,
        )

    def test_it_offers_the_columns_on_screen(self, window):
        dialog = self._dialog(window)
        offered = {label for label, _target in dialog.criteria.targets}
        assert {"Country", "Weight", "ID", "Name", "Status"} <= offered

    def test_the_operators_come_from_the_column_s_type(self, window):
        dialog = self._dialog(window)
        row = dialog.criteria.table
        column = row.cellWidget(0, 0)
        operator = row.cellWidget(0, 1)

        column.setCurrentIndex(column.findData("field:country"))
        text_ops = [operator.itemData(i) for i in range(operator.count())]
        assert "contains" in text_ops

        column.setCurrentIndex(column.findData("field:weight"))
        number_ops = [operator.itemData(i) for i in range(operator.count())]
        assert "gte" in number_ops
        assert "contains" not in number_ops

    def test_it_starts_with_one_empty_row_ready(self, window):
        assert self._dialog(window).criteria.table.rowCount() == 1

    def test_a_row_can_be_added_and_removed(self, window):
        dialog = self._dialog(window)
        dialog.criteria.add_row()
        assert dialog.criteria.table.rowCount() == 2
        dialog.criteria.table.setCurrentCell(1, 0)
        dialog.criteria._remove_row()
        assert dialog.criteria.table.rowCount() == 1

    def test_the_value_box_is_disabled_for_a_presence_test(self, window):
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:country"))
        operator.setCurrentIndex(operator.findData("empty"))
        assert not table.cellWidget(0, 2).isEnabled()

    def test_both_value_boxes_are_enabled_for_between(self, window):
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:weight"))
        operator.setCurrentIndex(operator.findData("between"))
        assert table.cellWidget(0, 2).isEnabled()
        assert table.cellWidget(0, 3).isEnabled()

    def test_what_it_builds_is_what_was_entered(self, window):
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:country"))
        operator.setCurrentIndex(operator.findData("is"))
        table.cellWidget(0, 2).setText("Japan")

        group = dialog.group()
        assert group.criteria == (Criterion("field:country", "is", ("Japan",)),)

    def test_a_list_of_ids_can_be_typed(self, window):
        """Requested: pick out particular coins by ID."""
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("__id__"))
        operator.setCurrentIndex(operator.findData("is_any_of"))
        table.cellWidget(0, 2).setText("1, 2\n3")

        assert dialog.group().criteria[0].values == ("1", "2", "3")

    def test_it_counts_the_matches_as_the_filter_is_built(self, window):
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:country"))
        operator.setCurrentIndex(operator.findData("is"))
        table.cellWidget(0, 2).setText("Japan")
        assert "1 coin(s) match" in dialog.summary.text()

    def test_it_says_when_nothing_is_filtered(self, window):
        assert "No filter" in self._dialog(window).summary.text()

    def test_a_half_filled_row_is_reported_rather_than_accepted(self, window):
        """Only one end of a 'between' is worse than useless: it must not be silently dropped."""
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:weight"))
        operator.setCurrentIndex(operator.findData("between"))
        table.cellWidget(0, 2).setText("4")

        dialog._accept_if_valid()
        assert dialog.result() != ACCEPTED
        assert "Weight" in dialog.summary.text()

    def test_an_untouched_row_is_a_placeholder_rather_than_a_question(self, window):
        """The dialog opens with a row to type into; an empty one must not filter anything."""
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:country"))
        operator.setCurrentIndex(operator.findData("is"))

        assert dialog.group().is_empty()
        assert "No filter" in dialog.summary.text()

    def test_it_opens_showing_the_filter_already_in_force(self, window):
        current = FilterGroup.of(Criterion("field:country", "is", ("Japan",)))
        dialog = self._dialog(window, current)
        assert dialog.group().criteria == current.criteria

    def test_a_subgroup_asks_the_question_a_flat_list_cannot(self, window):
        dialog = self._dialog(window)
        table = dialog.criteria.table
        column, operator = table.cellWidget(0, 0), table.cellWidget(0, 1)
        column.setCurrentIndex(column.findData("field:weight"))
        operator.setCurrentIndex(operator.findData("gte"))
        table.cellWidget(0, 2).setText("4")

        dialog.group_box.setChecked(True)
        dialog.subgroup.add_row()
        inner = dialog.subgroup.table
        for row, country in ((0, "China"), (1, "Britain")):
            if row >= inner.rowCount():
                dialog.subgroup.add_row()
            column = inner.cellWidget(row, 0)
            operator = inner.cellWidget(row, 1)
            column.setCurrentIndex(column.findData("field:country"))
            operator.setCurrentIndex(operator.findData("is"))
            inner.cellWidget(row, 2).setText(country)

        group = dialog.group()
        assert group.groups and group.groups[0].match == "any"
        assert len(group.groups[0].criteria) == 2

    def test_clearing_empties_the_dialog(self, window):
        current = FilterGroup.of(Criterion("field:country", "is", ("Japan",)))
        dialog = self._dialog(window, current)
        dialog._clear()
        assert dialog.group().is_empty()
