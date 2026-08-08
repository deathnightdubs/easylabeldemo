"""The grid: editing, block operations, undo, sorting and the Trash."""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtGui import QGuiApplication

from numis.ui.commands import AddSpecimens, DeleteSpecimens, SetSortValue

EDIT = Qt.ItemDataRole.EditRole
DISPLAY = Qt.ItemDataRole.DisplayRole
CLEAR = QItemSelectionModel.SelectionFlag.ClearAndSelect

#: Column positions in the fixture. The first three are the identity columns.
ID, NAME, SUBCOLLECTION = 0, 1, 2
RULER, DENOM, DATE, WEIGHT = 3, 4, 5, 6


def add_rows(model, count: int):
    model.undo.push(AddSpecimens(model.service, model.subcollection.id, count))
    model.refresh()


def set_cell(model, row: int, column: int, text: str) -> bool:
    return model.setData(model.index(row, column), text, EDIT)


def cell(model, row: int, column: int) -> str:
    return str(model.data(model.index(row, column), DISPLAY) or "")


def column_values(model, column: int) -> list[str]:
    return [cell(model, row, column) for row in range(model.rowCount())]


class TestGrid:
    def test_headers_use_the_subcollections_labels(self, sheet):
        model, _ = sheet
        headers = [
            model.headerData(index, Qt.Orientation.Horizontal)
            for index in range(model.columnCount())
        ]
        assert headers == [
            "ID",
            "Name",
            "Subcollection",
            "Ruler",
            "Denomination",
            "Date",
            "Weight",
        ]

    def test_the_identity_columns_are_editable(self, sheet):
        """ID, Name and Subcollection are all editable, like any other column."""
        model, _ = sheet
        add_rows(model, 1)
        for section in (ID, NAME, SUBCOLLECTION):
            assert model.flags(model.index(0, section)) & Qt.ItemFlag.ItemIsEditable

    def test_every_row_gets_an_id_without_being_asked(self, sheet):
        model, _ = sheet
        add_rows(model, 3)
        codes = column_values(model, ID)
        assert all(codes)
        assert len(set(codes)) == 3

    def test_typing_into_a_cell_stores_a_parsed_value(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        assert set_cell(model, 0, WEIGHT, "27.15 g")
        assert cell(model, 0, WEIGHT) == "27.15 g"

    def test_units_are_converted_on_entry(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, WEIGHT, "1 ozt")
        assert cell(model, 0, WEIGHT) == "31.10 g"

    def test_an_unreadable_value_is_refused_with_a_message(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        messages: list[str] = []
        model.error.connect(messages.append)

        assert set_cell(model, 0, WEIGHT, "5 stone") is False
        assert cell(model, 0, WEIGHT) == ""
        assert messages and "unknown mass unit" in messages[0]
        assert "Weight" in messages[0]  # the field is named, not just the error

    def test_a_refused_edit_does_not_enter_the_undo_history(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        before = model.undo.count()
        set_cell(model, 0, WEIGHT, "nonsense")
        assert model.undo.count() == before

    def test_clearing_a_cell_removes_the_value(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, RULER, "Victoria")
        set_cell(model, 0, RULER, "")
        assert cell(model, 0, RULER) == ""


class TestBlockOperations:
    def test_paste_fills_from_the_current_cell(self, sheet):
        model, view = sheet
        add_rows(model, 3)
        QGuiApplication.clipboard().setText("Victoria\t1 Crown\nGeorge V\t1 Florin")
        view.selectionModel().select(model.index(0, RULER), CLEAR)
        view.paste_selection()

        assert column_values(model, RULER) == ["Victoria", "George V", ""]
        assert column_values(model, DENOM) == ["1 Crown", "1 Florin", ""]

    def test_paste_of_one_value_fills_the_whole_selection(self, sheet):
        model, view = sheet
        add_rows(model, 3)
        QGuiApplication.clipboard().setText("Victoria")
        selection = QItemSelection(model.index(0, RULER), model.index(2, RULER))
        view.selectionModel().select(selection, CLEAR)
        view.paste_selection()
        assert column_values(model, RULER) == ["Victoria"] * 3

    def test_paste_is_one_undo_step(self, sheet):
        model, view = sheet
        add_rows(model, 2)
        QGuiApplication.clipboard().setText("a\tb\nc\td")
        view.selectionModel().select(model.index(0, RULER), CLEAR)
        view.paste_selection()

        model.undo.undo()
        model.refresh()
        assert column_values(model, RULER) == ["", ""]
        assert column_values(model, DENOM) == ["", ""]

    def test_paste_skips_values_that_cannot_be_read(self, sheet):
        model, view = sheet
        add_rows(model, 2)
        messages: list[str] = []
        model.error.connect(messages.append)
        # Second row's weight is nonsense; the first must still apply.
        QGuiApplication.clipboard().setText("27.15\nrubbish")
        view.selectionModel().select(model.index(0, WEIGHT), CLEAR)
        view.paste_selection()

        assert cell(model, 0, WEIGHT) == "27.15 g"
        assert cell(model, 1, WEIGHT) == ""
        assert messages and "skipped" in messages[0]

    def test_fill_down_copies_the_top_cell(self, sheet):
        model, view = sheet
        add_rows(model, 3)
        set_cell(model, 0, RULER, "Victoria")
        selection = QItemSelection(model.index(0, RULER), model.index(2, RULER))
        view.selectionModel().select(selection, CLEAR)
        view.fill_down()
        assert column_values(model, RULER) == ["Victoria"] * 3

    def test_copy_produces_spreadsheet_text(self, sheet):
        model, view = sheet
        add_rows(model, 2)
        set_cell(model, 0, RULER, "Victoria")
        set_cell(model, 1, RULER, "George V")
        selection = QItemSelection(model.index(0, RULER), model.index(1, DENOM))
        view.selectionModel().select(selection, CLEAR)
        view.copy_selection()
        assert QGuiApplication.clipboard().text() == "Victoria\t\nGeorge V\t"

    def test_clear_contents_empties_a_selection_in_one_step(self, sheet):
        model, view = sheet
        add_rows(model, 2)
        set_cell(model, 0, RULER, "Victoria")
        set_cell(model, 1, RULER, "George V")
        selection = QItemSelection(model.index(0, RULER), model.index(1, RULER))
        view.selectionModel().select(selection, CLEAR)
        view.clear_selection()
        assert column_values(model, RULER) == ["", ""]

        model.undo.undo()
        model.refresh()
        assert column_values(model, RULER) == ["Victoria", "George V"]


class TestUndo:
    def test_undo_restores_the_previous_value(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, RULER, "Victoria")
        set_cell(model, 0, RULER, "George V")

        model.undo.undo()
        model.refresh()
        assert cell(model, 0, RULER) == "Victoria"

        model.undo.redo()
        model.refresh()
        assert cell(model, 0, RULER) == "George V"

    def test_undo_preserves_a_manually_chosen_sort_value(self, sheet):
        """Re-parsing the display text would silently lose it, which is why undo restores
        the stored columns instead."""
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, DATE, "Guangxu year 30")
        field = model.field_at(DATE)
        specimen = model.specimen_at(0)

        model.undo.push(SetSortValue(model.service, specimen.id, field.id, 1904))
        model.refresh()
        assert model.service.raw_columns(specimen, field)["sort_value"] == 1904.0

        # An unrelated edit, then undo it: the manual sort value must survive.
        set_cell(model, 0, RULER, "Guangxu")
        model.undo.undo()
        model.refresh()
        assert model.service.raw_columns(specimen, field)["sort_value"] == 1904.0

    def test_undoing_a_sort_value_puts_the_guess_back(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, DATE, "1736-1795")
        field, specimen = model.field_at(DATE), model.specimen_at(0)

        model.undo.push(SetSortValue(model.service, specimen.id, field.id, 1750))
        model.refresh()
        assert model.service.raw_columns(specimen, field)["sort_source"] == "manual"

        model.undo.undo()
        model.refresh()
        columns = model.service.raw_columns(specimen, field)
        assert columns["sort_source"] == "auto"
        assert columns["sort_value"] == 1765.5

    def test_undoing_added_rows_removes_them(self, sheet):
        model, _ = sheet
        add_rows(model, 3)
        assert model.rowCount() == 3
        model.undo.undo()
        model.refresh()
        assert model.rowCount() == 0

    def test_commands_are_described_for_the_undo_menu(self, sheet):
        model, view = sheet
        add_rows(model, 2)
        set_cell(model, 0, RULER, "Victoria")
        selection = QItemSelection(model.index(0, RULER), model.index(1, RULER))
        view.selectionModel().select(selection, CLEAR)
        view.fill_down()
        described = [model.undo.command(i).text() for i in range(model.undo.count())]
        assert described == ["add 2 rows", "edit cell", "fill down 1 cells"]


class TestSortingAndFlags:
    def test_dates_sort_by_their_numeric_key(self, sheet):
        model, _ = sheet
        add_rows(model, 4)
        for row, text in enumerate(["1943", "c. 350 BC", "1736-1795", "undated"]):
            set_cell(model, row, DATE, text)

        model.sort(DATE, Qt.SortOrder.AscendingOrder)
        assert column_values(model, DATE) == ["c. 350 BC", "1736-1795", "1943", "undated"]

    def test_denominations_sort_numerically_when_asked(self, sheet):
        model, _ = sheet
        add_rows(model, 3)
        for row, text in enumerate(["100 cash", "1 wen", "10 wen"]):
            set_cell(model, row, DENOM, text)
        model.sort(DENOM, Qt.SortOrder.AscendingOrder)
        assert column_values(model, DENOM) == ["1 wen", "10 wen", "100 cash"]

    def test_guessed_sort_positions_are_flagged(self, sheet):
        model, _ = sheet
        add_rows(model, 2)
        set_cell(model, 0, DATE, "1736-1795")  # a range: the midpoint is a guess
        set_cell(model, 1, DATE, "1943")  # nothing to confirm
        assert model.is_flagged(model.index(0, DATE))
        assert not model.is_flagged(model.index(1, DATE))

    def test_setting_a_sort_value_clears_the_flag(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, DATE, "1736-1795")
        field, specimen = model.field_at(DATE), model.specimen_at(0)
        model.undo.push(SetSortValue(model.service, specimen.id, field.id, 1765))
        model.refresh()
        assert not model.is_flagged(model.index(0, DATE))

    def test_ordinary_text_is_never_flagged(self, sheet):
        model, _ = sheet
        add_rows(model, 1)
        set_cell(model, 0, RULER, "Maria Theresia")
        assert not model.is_flagged(model.index(0, RULER))


class TestTrash:
    def test_deleted_rows_disappear_but_are_recoverable(self, sheet):
        model, _ = sheet
        add_rows(model, 2)
        model.undo.push(DeleteSpecimens(model.service, [model.specimen_at(0).id]))
        model.refresh()
        assert model.rowCount() == 1

        model.undo.undo()
        model.refresh()
        assert model.rowCount() == 2

    def test_the_trash_is_visible_while_a_sort_is_active(self, sheet):
        """Regression: the sorted path ignored the Trash setting entirely."""
        model, _ = sheet
        add_rows(model, 3)
        for row, text in enumerate(["1 wen", "10 wen", "100 cash"]):
            set_cell(model, row, DENOM, text)
        model.sort(DENOM, Qt.SortOrder.AscendingOrder)

        model.undo.push(DeleteSpecimens(model.service, [model.specimen_at(0).id]))
        model.refresh()
        assert model.rowCount() == 2

        model.set_show_trash(True)
        assert model.rowCount() == 3

    def test_the_trash_is_visible_without_a_sort(self, sheet):
        model, _ = sheet
        add_rows(model, 2)
        model.undo.push(DeleteSpecimens(model.service, [model.specimen_at(0).id]))
        model.refresh()
        model.set_show_trash(True)
        assert model.rowCount() == 2


class TestSearch:
    def test_search_filters_the_grid(self, sheet):
        model, _ = sheet
        add_rows(model, 2)
        set_cell(model, 0, RULER, "Maria Theresia")
        set_cell(model, 1, RULER, "Victoria")
        for row in range(2):
            model.service.reindex(model.specimen_at(row))

        model.set_search("Theresia")
        assert model.rowCount() == 1
        assert cell(model, 0, RULER) == "Maria Theresia"

        model.set_search("")
        assert model.rowCount() == 2

    def test_two_character_cjk_search_works_from_the_grid(self, sheet):
        model, _ = sheet
        add_rows(model, 2)
        set_cell(model, 0, RULER, "乾隆通寶 寶泉")
        set_cell(model, 1, RULER, "咸豐重寶 當十")
        for row in range(2):
            model.service.reindex(model.specimen_at(row))

        model.set_search("通寶")
        assert model.rowCount() == 1
        assert cell(model, 0, RULER) == "乾隆通寶 寶泉"


class TestMasterView:
    def test_a_shared_column_merges_under_its_canonical_label(self, sheet, svc, ancients):
        model, _ = sheet
        head = svc.field_by_key("ruler")
        svc.show_field(ancients, head, display_label="Emperor", show_in_table=True)

        model.set_subcollection(ancients)
        headers = [
            model.headerData(index, Qt.Orientation.Horizontal)
            for index in range(model.columnCount())
        ]
        assert "Emperor" in headers

        model.set_subcollection(None)  # the master view
        headers = [
            model.headerData(index, Qt.Orientation.Horizontal)
            for index in range(model.columnCount())
        ]
        assert "Ruler" in headers
        assert "Emperor" not in headers
