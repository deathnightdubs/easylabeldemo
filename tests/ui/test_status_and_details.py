"""Sold/disposed coins, reusable identifiers, and the special-system editors."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from sqlalchemy.exc import IntegrityError

from numis.db import create_library
from numis.models import CatalogReference, ExternalLink, SpecimenGrade
from numis.ui.commands import AddChildRow, DeleteChildRow, SetInventoryCode, SetStatus
from numis.ui.main_window import MainWindow

EDIT = Qt.ItemDataRole.EditRole
DISPLAY = Qt.ItemDataRole.DisplayRole
ID_COL, NAME_COL, SUB_COL, STATUS_COL = 0, 1, 2, 3


def _sheldon(service):
    """A grading scale in the window's own library.

    Deliberately not the shared ``sheldon`` fixture: that lives in a different database, so
    mixing them silently produces a scale the window cannot see.
    """
    scale = service.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")
    for label, normalised in [("MS64", 64.0), ("MS63", 63.0), ("MS62", 62.0)]:
        service.add_grade_level(scale, label, normalised, numeric_value=float(label[2:]))
    return scale


@pytest.fixture
def window(qapp, tmp_path):
    library = create_library(tmp_path / "Status.numis")
    win = MainWindow(library)
    service = win.service
    modern = service.create_subcollection("Modern")
    field = service.create_field("ruler", "Ruler", "text")
    service.show_field(modern, field, show_in_table=True)
    for _ in range(3):
        service.add_specimen(modern)
    win.session.commit()
    win._reload_subcollections(keep="Modern")
    yield win
    win.session.close()
    library.close()


class TestSoldAndHidden:
    def test_sold_coins_are_hidden_by_default(self, window):
        model = window.model
        assert model.rowCount() == 3

        window.view.selectRow(0)
        window._set_status("sold")
        assert model.rowCount() == 2

    def test_showing_them_brings_them_back_in_italics(self, window):
        model = window.model
        window.view.selectRow(0)
        window._set_status("sold")

        window.disposed_action.setChecked(True)
        assert model.rowCount() == 3

        row = next(
            r
            for r in range(model.rowCount())
            if model.data(model.index(r, STATUS_COL), DISPLAY) == "sold"
        )
        font = model.data(model.index(row, NAME_COL), Qt.ItemDataRole.FontRole)
        assert font is not None and font.italic()
        assert not font.strikeOut()  # not deleted: it was yours and its history stands

    def test_the_status_column_can_be_typed_into(self, window):
        model = window.model
        assert model.setData(model.index(0, STATUS_COL), "sold", EDIT)
        assert model.rowCount() == 2

    def test_an_unknown_status_is_refused_and_lists_the_options(self, window):
        model = window.model
        messages: list[str] = []
        model.error.connect(messages.append)
        assert model.setData(model.index(0, STATUS_COL), "flogged", EDIT) is False
        assert messages and "owned" in messages[0]

    def test_marking_as_sold_is_undoable(self, window):
        window.view.selectRow(0)
        window._set_status("sold")
        assert window.model.rowCount() == 2

        window.undo.undo()
        assert window.model.rowCount() == 3

    def test_a_sold_coin_keeps_its_data_and_history(self, window):
        service, model = window.service, window.model
        model.setData(model.index(0, 4), "Victoria", EDIT)
        specimen = model.specimen_at(0)
        service.add_event(specimen, "acquired", amount="10.00")
        window.session.commit()

        window.undo.push(SetStatus(service, [specimen.id], "sold"))
        window.model.refresh()

        assert service.display(specimen, "ruler") == "Victoria"
        assert service.cost_basis(specimen) == 1000

    def test_recording_a_sale_in_the_ledger_also_marks_it_sold(self, window):
        """The ledger stays the record of transactions; status follows it."""
        service, model = window.service, window.model
        specimen = model.specimen_at(0)
        service.add_event(specimen, "sold", amount="20.00")
        window.session.commit()
        assert specimen.status == "sold"

    def test_disposed_and_trash_are_separate(self, window):
        model = window.model
        window.view.selectRow(0)
        window._set_status("sold")
        window.view.selectRow(0)
        window._delete_rows()
        assert model.rowCount() == 1

        window.disposed_action.setChecked(True)
        assert model.rowCount() == 2  # the sold one returns, the deleted one does not
        window.trash_action.setChecked(True)
        assert model.rowCount() == 3


class TestReusingIdentifiers:
    def test_an_id_from_the_trash_can_be_reused_once_confirmed(self, window):
        model = window.model
        doomed = model.specimen_at(2)
        code = doomed.inventory_code
        window.view.selectRow(2)
        window._delete_rows()

        model.confirm_reuse = lambda *_: True  # stand in for the confirmation dialog
        assert model.setData(model.index(0, ID_COL), code, EDIT)
        assert model.data(model.index(0, ID_COL), DISPLAY) == code

    def test_declining_the_prompt_leaves_it_alone(self, window):
        model = window.model
        code = model.specimen_at(2).inventory_code
        original = model.specimen_at(0).inventory_code
        window.view.selectRow(2)
        window._delete_rows()

        model.confirm_reuse = lambda *_: False
        assert model.setData(model.index(0, ID_COL), code, EDIT) is False
        assert model.data(model.index(0, ID_COL), DISPLAY) == original

    def test_an_id_in_use_is_still_refused_outright(self, window):
        model = window.model
        messages: list[str] = []
        model.error.connect(messages.append)
        model.confirm_reuse = lambda *_: True

        in_use = model.data(model.index(1, ID_COL), DISPLAY)
        assert model.setData(model.index(0, ID_COL), in_use, EDIT) is False
        assert messages and "already used" in messages[0]

    def test_restoring_a_coin_whose_id_was_taken_gives_it_a_new_one(self, window):
        service, model = window.service, window.model
        doomed = model.specimen_at(2)
        code = doomed.inventory_code
        window.view.selectRow(2)
        window._delete_rows()

        model.confirm_reuse = lambda *_: True
        model.setData(model.index(0, ID_COL), code, EDIT)

        reassigned = service.restore(doomed)
        window.session.commit()
        assert reassigned is not None
        assert doomed.inventory_code != code
        assert doomed.deleted_at is None

    def test_reuse_is_undoable_and_returns_the_code(self, window):
        service, model = window.service, window.model
        doomed = model.specimen_at(2)
        code = doomed.inventory_code
        window.view.selectRow(2)
        window._delete_rows()

        window.undo.push(
            SetInventoryCode(service, model.specimen_at(0).id, code, reuse_from_trash=True)
        )
        window.undo.undo()
        window.model.refresh()
        assert doomed.inventory_code == code


class TestDetailPanel:
    def test_it_follows_the_selection(self, window):
        window.view.selectRow(1)
        assert window.detail.specimen is window.model.specimen_at(1)

    def test_it_empties_when_nothing_is_selected(self, window):
        window.view.clearSelection()
        window._selection_changed()
        assert window.detail.specimen is None
        assert "No coin selected" in window.detail.title.text()

    def test_a_catalogue_number_can_be_added_and_removed(self, window):
        service, detail = window.service, window.detail
        window.view.selectRow(0)
        krause = service.create_catalog("KM", "Krause")
        window.session.commit()
        specimen_id, catalog_id = detail.specimen.id, krause.id

        detail._apply(
            AddChildRow(
                service,
                "add catalogue number",
                lambda svc: svc.add_reference(
                    svc.session.get(type(detail.specimen), specimen_id),
                    svc.session.get(type(krause), catalog_id),
                    "2073",
                    is_primary=True,
                ),
            )
        )
        assert detail.catalogues.list.count() == 1
        assert "KM 2073" in detail.catalogues.list.item(0).text()

        window.undo.undo()
        detail.refresh()
        assert detail.catalogues.list.count() == 0

    def test_removing_a_grade_is_reversible(self, window):
        service, detail = window.service, window.detail
        window.view.selectRow(0)
        scale = _sheldon(service)
        grade = service.add_grade(
            detail.specimen, scale, "MS63", source="tpg", assigned_by="NGC", is_primary=True
        )
        window.session.commit()
        detail.refresh()
        assert detail.grades.list.count() == 1

        detail._apply(DeleteChildRow(service, "remove grade", SpecimenGrade, grade.id))
        assert detail.grades.list.count() == 0

        window.undo.undo()
        detail.refresh()
        assert detail.grades.list.count() == 1
        assert "MS63" in detail.grades.list.item(0).text()

    def test_a_removed_grade_comes_back_with_its_modifiers(self, window):
        service, detail = window.service, window.detail
        window.view.selectRow(0)
        scale = _sheldon(service)
        service.create_grade_modifier("DETAILS", "Details", "detail", -0.4)
        grade = service.add_grade(
            detail.specimen, scale, "MS63", modifiers=["DETAILS"], is_primary=True
        )
        window.session.commit()
        assert grade.normalised == pytest.approx(62.6)

        detail._apply(DeleteChildRow(service, "remove grade", SpecimenGrade, grade.id))
        window.undo.undo()
        detail.refresh()

        restored = service.primary_grade(detail.specimen)
        assert restored is not None
        assert [m.code for m in restored.modifiers] == ["DETAILS"]
        assert restored.normalised == pytest.approx(62.6)

    def test_links_are_listed_and_removable(self, window):
        service, detail = window.service, window.detail
        window.view.selectRow(0)
        specimen_id = detail.specimen.id

        detail._apply(
            AddChildRow(
                service,
                "add link",
                lambda svc: svc.add_link(
                    svc.session.get(type(detail.specimen), specimen_id),
                    "https://zeno.ru/showphoto.php?photo=12345",
                    kind="zeno",
                    label="Zeno record",
                ),
            )
        )
        assert detail.links.list.count() == 1
        assert "Zeno record" in detail.links.list.item(0).text()

        link_id = detail.links.selected_id() or detail.links.list.item(0).data(
            Qt.ItemDataRole.UserRole
        )
        detail._apply(DeleteChildRow(service, "remove link", ExternalLink, link_id))
        assert detail.links.list.count() == 0

    def test_a_duplicate_catalogue_number_does_not_leave_a_broken_session(self, window):
        service, detail = window.service, window.detail
        window.view.selectRow(0)
        krause = service.create_catalog("KM", "Krause")
        window.session.commit()
        service.add_reference(detail.specimen, krause, "2073")
        window.session.commit()

        specimen_id, catalog_id = detail.specimen.id, krause.id
        with pytest.raises(IntegrityError):
            detail._apply(
                AddChildRow(
                    service,
                    "add catalogue number",
                    lambda svc: svc.add_reference(
                        svc.session.get(type(detail.specimen), specimen_id),
                        svc.session.get(CatalogReference, 1).catalog,
                        "2073",
                    ),
                )
            )
        service.session.rollback()
        # The session must still be usable afterwards.
        assert service.references_for(service.session.get(type(detail.specimen), specimen_id))
        del catalog_id
