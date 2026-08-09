"""Changing what a special-system column shows, from the header down to the stored setting."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from numis.columns import ColumnDisplay
from numis.db import create_library
from numis.ui import main_window as window_module
from numis.ui.column_settings import ColumnSettingsDialog
from numis.ui.main_window import MainWindow

DISPLAY = Qt.ItemDataRole.DisplayRole
ACCEPTED = QDialog.DialogCode.Accepted


@pytest.fixture
def window(qapp, tmp_path):
    """A window whose one subcollection shows a catalogue and a grade column."""
    library = create_library(tmp_path / "Columns.numis")
    win = MainWindow(library)
    service = win.service
    modern = service.create_subcollection("Modern")
    service.show_special_block(modern, "catalogues", display_label="Catalogue", show_in_table=True)
    service.show_special_block(modern, "grades", display_label="Grade", show_in_table=True)

    krause = service.create_catalog("KM", "Krause")
    hartill = service.create_catalog("H", "Hartill")
    scale = service.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")
    service.create_grade_modifier("CAC", "CAC sticker", "sticker", 0.15, issuer="CAC")

    coin = service.add_specimen(modern, display_name="Cash coin")
    service.add_reference(coin, krause, "1866", rank=1)
    service.add_reference(coin, hartill, "1.01", rank=2)
    service.add_grade(
        coin, scale, "MS63", base_value=63.0,
        modifiers=[("CAC", "Gold")], source="tpg", assigned_by="PCGS", rank=1,
    )
    win.session.commit()
    win._reload_subcollections(keep="Modern")
    yield win
    win.session.close()
    library.close()


def _section(window, label: str) -> int:
    model = window.model
    for section in range(model.columnCount()):
        if model.headerData(section, Qt.Orientation.Horizontal) == label:
            return section
    raise AssertionError(f"no {label!r} column")


def _cell(window, label: str, row: int = 0) -> str:
    return window.model.data(window.model.index(row, _section(window, label)), DISPLAY)


def _menu_labels(view, section: int) -> list[str]:
    return [action.text() for action in view.header_menu(section).actions()]


class TestTheHeaderOffersSettings:
    def test_a_special_column_offers_them(self, window):
        assert "Column settings…" in _menu_labels(window.view, _section(window, "Catalogue"))

    def test_an_ordinary_field_does_not(self, window):
        field = window.service.create_field("ruler", "Ruler", "text")
        window.service.show_field(
            window.current_subcollection(), field, show_in_table=True, sort_order=9
        )
        window.session.commit()
        window.model.refresh()

        assert "Column settings…" not in _menu_labels(window.view, _section(window, "Ruler"))

    def test_the_identity_columns_do_not_either(self, window):
        assert "Column settings…" not in _menu_labels(window.view, 0)

    def test_choosing_it_asks_the_window(self, window):
        asked: list[int] = []
        # The window answers this by opening a modal, which a headless run cannot dismiss, so
        # listen in its place.
        window.view.column_settings_requested.disconnect()
        window.view.column_settings_requested.connect(asked.append)
        section = _section(window, "Grade")

        for action in window.view.header_menu(section).actions():
            if action.text() == "Column settings…":
                action.trigger()

        assert asked == [section]

    def test_the_usual_header_actions_are_still_there(self, window):
        labels = _menu_labels(window.view, _section(window, "Grade"))
        assert any("Hide" in label for label in labels)
        assert "Fit columns to contents" in labels


class TestSavingFromTheDialog:
    def _accept_with(self, monkeypatch, **settings):
        """Stand in for the user opening the dialog and choosing settings."""

        class Chosen(ColumnSettingsDialog):
            def exec(self):
                return ACCEPTED

            def display(self):
                return ColumnDisplay(**settings)

        monkeypatch.setattr(window_module, "ColumnSettingsDialog", Chosen)

    def test_the_grid_redraws_with_the_new_settings(self, window, monkeypatch):
        assert _cell(window, "Catalogue") == "KM 1866 · H 1.01"

        self._accept_with(monkeypatch, mode="only", only="H", show_catalogue=False)
        window._column_settings(_section(window, "Catalogue"))

        assert _cell(window, "Catalogue") == "1.01"

    def test_the_setting_is_stored_on_the_column(self, window, monkeypatch):
        self._accept_with(monkeypatch, mode="rank", rank=2)
        window._column_settings(_section(window, "Catalogue"))

        block = window.service.block_for(window.current_subcollection(), "catalogues")
        assert window.service.block_display(block).mode == "rank"
        assert window.service.block_display(block).rank == 2

    def test_it_survives_closing_and_reopening_the_library(self, window, monkeypatch, tmp_path):
        self._accept_with(monkeypatch, mode="only", only="H")
        window._column_settings(_section(window, "Catalogue"))
        path = window.library.path
        window.session.close()
        window.library.close()

        from numis.db import open_library

        library = open_library(path)
        reopened = MainWindow(library)
        try:
            reopened._reload_subcollections(keep="Modern")
            assert _cell(reopened, "Catalogue") == "H 1.01"
        finally:
            reopened.session.close()
            library.close()

    def test_grade_options_reach_the_cell(self, window, monkeypatch):
        assert _cell(window, "Grade") == "MS63 CAC"

        self._accept_with(monkeypatch, modifier_details=True, show_assigned_by=True)
        window._column_settings(_section(window, "Grade"))

        assert _cell(window, "Grade") == "MS63 CAC Gold [PCGS]"

    def test_cancelling_changes_nothing(self, window, monkeypatch):
        before = _cell(window, "Catalogue")

        class Cancelled(ColumnSettingsDialog):
            def exec(self):
                return QDialog.DialogCode.Rejected

            def display(self):
                return ColumnDisplay(mode="rank", rank=2)

        monkeypatch.setattr(window_module, "ColumnSettingsDialog", Cancelled)
        window._column_settings(_section(window, "Catalogue"))

        assert _cell(window, "Catalogue") == before

    def test_it_says_what_the_column_now_shows(self, window, monkeypatch):
        self._accept_with(monkeypatch, mode="only", only="H")
        window._column_settings(_section(window, "Catalogue"))
        assert "Only catalogue H" in window.statusBar().currentMessage()

    def test_an_ordinary_field_column_is_left_alone(self, window, monkeypatch):
        opened: list[bool] = []

        class Watched(ColumnSettingsDialog):
            def exec(self):
                opened.append(True)
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(window_module, "ColumnSettingsDialog", Watched)
        window._column_settings(0)  # the ID column
        assert opened == []


class TestTheMasterView:
    def test_it_explains_that_settings_belong_to_a_subcollection(self, window, monkeypatch):
        opened: list[bool] = []

        class Watched(ColumnSettingsDialog):
            def exec(self):
                opened.append(True)
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(window_module, "ColumnSettingsDialog", Watched)
        window._reload_subcollections(keep=window_module.MASTER_VIEW)
        assert window.current_subcollection() is None

        window._column_settings(_section(window, "Catalogue"))

        assert opened == [], "the dialog must not open where there is no single column to edit"
        assert "subcollection" in window.statusBar().currentMessage()


class TestTheDialogItself:
    def _dialog(self, window, kind: str, display: ColumnDisplay | None = None):
        return ColumnSettingsDialog(
            window.service,
            window.current_subcollection(),
            kind,
            display or ColumnDisplay(),
            window,
        )

    def test_it_opens_showing_the_current_settings(self, window):
        dialog = self._dialog(window, "catalogues", ColumnDisplay(mode="rank", rank=3))
        assert dialog.mode_rank.isChecked()
        assert dialog.rank.value() == 3

    def test_what_it_returns_is_what_was_chosen(self, window):
        dialog = self._dialog(window, "catalogues")
        dialog.mode_only.setChecked(True)
        dialog.only.setCurrentText("H")
        dialog.show_catalogue.setChecked(False)

        chosen = dialog.display()
        assert chosen.mode == "only"
        assert chosen.only == "H"
        assert chosen.show_catalogue is False

    def test_the_filter_is_offered_the_collection_s_own_catalogues(self, window):
        dialog = self._dialog(window, "catalogues")
        choices = [dialog.only.itemText(i) for i in range(dialog.only.count())]
        assert choices == ["H", "KM"]

    def test_a_grade_filter_offers_the_graders_actually_used(self, window):
        dialog = self._dialog(window, "grades")
        choices = [dialog.only.itemText(i) for i in range(dialog.only.count())]
        assert "PCGS" in choices
        assert "tpg" in choices, "the sources are offered alongside the names"

    def test_the_preview_shows_a_real_row(self, window):
        dialog = self._dialog(window, "catalogues")
        assert "KM 1866 · H 1.01" in dialog.preview.text()
        assert "Cash coin" in dialog.preview.text()

    def test_the_preview_follows_the_settings(self, window):
        dialog = self._dialog(window, "catalogues")
        dialog.mode_only.setChecked(True)
        dialog.only.setCurrentText("H")
        assert "H 1.01" in dialog.preview.text()
        assert "KM 1866" not in dialog.preview.text()

    def test_the_preview_says_so_when_the_settings_match_nothing(self, window):
        dialog = self._dialog(window, "catalogues")
        dialog.mode_rank.setChecked(True)
        dialog.rank.setValue(5)
        assert "blank" in dialog.preview.text()

    def test_only_the_relevant_input_is_enabled(self, window):
        dialog = self._dialog(window, "catalogues")
        dialog.mode_all.setChecked(True)
        assert not dialog.only.isEnabled()
        assert not dialog.rank.isEnabled()

        dialog.mode_only.setChecked(True)
        assert dialog.only.isEnabled()
        assert not dialog.rank.isEnabled()

        dialog.mode_rank.setChecked(True)
        assert dialog.rank.isEnabled()
        assert not dialog.only.isEnabled()

    def test_spelling_out_modifiers_needs_modifiers_shown(self, window):
        dialog = self._dialog(window, "grades")
        dialog.show_modifiers.setChecked(False)
        assert not dialog.modifier_details.isEnabled()
        dialog.show_modifiers.setChecked(True)
        assert dialog.modifier_details.isEnabled()

    def test_the_rank_option_names_the_position(self, window):
        dialog = self._dialog(window, "catalogues")
        dialog.mode_rank.setChecked(True)
        dialog.rank.setValue(2)
        assert "second" in dialog.mode_rank.text()

    def test_a_links_column_is_offered_listing_instead_of_counting(self, window):
        dialog = self._dialog(window, "links")
        assert hasattr(dialog, "show_labels")
        dialog.show_labels.setChecked(True)
        assert dialog.display().show_labels is True

    def test_a_subcollection_with_no_coins_previews_gracefully(self, qapp, window):
        empty = window.service.create_subcollection("Empty")
        window.session.commit()
        dialog = ColumnSettingsDialog(
            window.service, empty, "catalogues", ColumnDisplay(), window
        )
        assert "nothing" in dialog.preview.text()


def _header_tip(window, label: str):
    section = _section(window, label)
    return window.model.headerData(
        section, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole
    )


class TestTheHeaderExplainsItself:
    def test_the_tooltip_says_what_the_column_shows(self, window):
        assert "All entries" in _header_tip(window, "Catalogue")

    def test_it_updates_when_the_settings_change(self, window):
        block = window.service.block_for(window.current_subcollection(), "catalogues")
        window.service.set_block_display(block, ColumnDisplay(mode="only", only="H"))
        window.session.commit()
        window.model.refresh()

        assert "Only catalogue H" in _header_tip(window, "Catalogue")

    def test_an_ordinary_field_has_no_such_tooltip(self, window):
        field = window.service.create_field("ruler", "Ruler", "text")
        window.service.show_field(
            window.current_subcollection(), field, show_in_table=True, sort_order=9
        )
        window.session.commit()
        window.model.refresh()

        assert _header_tip(window, "Ruler") is None
