"""Special-system columns in the grid.

Catalogue numbers, grades, certifications and links have no editors yet, but they can be
placed as columns, so the grid must render them read-only without falling over.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoStack

from numis.ui.table_model import SpecimenTableModel

DISPLAY = Qt.ItemDataRole.DisplayRole


def _model(svc, modern, undo: QUndoStack) -> SpecimenTableModel:
    model = SpecimenTableModel(svc, undo)
    model.set_subcollection(modern)
    return model


def test_a_catalogue_column_shows_the_numbers(qapp, svc, modern, undo):
    svc.show_special_block(modern, "catalogues", display_label="References", show_in_table=True)
    krause = svc.create_catalog("KM", "Krause")
    coin = svc.add_specimen(modern, display_name="Thaler")
    svc.add_reference(coin, krause, "KM#1866", is_primary=True)

    model = _model(svc, modern, undo)
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
    ]
    assert "References" in headers
    assert model.data(model.index(0, headers.index("References")), DISPLAY) == "KM 1866"


def test_special_columns_are_not_editable_yet(qapp, svc, modern, undo):
    svc.show_special_block(modern, "catalogues", show_in_table=True)
    svc.add_specimen(modern)
    model = _model(svc, modern, undo)
    section = model.columnCount() - 1
    assert not model.flags(model.index(0, section)) & Qt.ItemFlag.ItemIsEditable


def test_a_grade_column_shows_the_primary_grade(qapp, svc, modern, undo, sheldon):
    svc.show_special_block(modern, "grades", display_label="Grade", show_in_table=True)
    coin = svc.add_specimen(modern)
    svc.add_grade(coin, sheldon, "MS63", source="tpg", assigned_by="NGC", is_primary=True)

    model = _model(svc, modern, undo)
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
    ]
    assert model.data(model.index(0, headers.index("Grade")), DISPLAY) == "MS63"


def test_a_certification_column_shows_current_certifications(qapp, svc, modern, undo):
    svc.show_special_block(modern, "certifications", display_label="Cert", show_in_table=True)
    coin = svc.add_specimen(modern)
    ngc = svc.create_grading_company("NGC", "NGC")
    svc.add_certification(coin, ngc, cert_number="2871554-013", is_primary=True)

    model = _model(svc, modern, undo)
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
    ]
    assert "2871554-013" in model.data(model.index(0, headers.index("Cert")), DISPLAY)


def test_a_links_column_counts_them(qapp, svc, modern, undo):
    svc.show_special_block(modern, "links", display_label="Links", show_in_table=True)
    coin = svc.add_specimen(modern)
    svc.add_link(coin, "https://example.invalid/a")
    svc.add_link(coin, "https://example.invalid/b")

    model = _model(svc, modern, undo)
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
    ]
    assert model.data(model.index(0, headers.index("Links")), DISPLAY) == "2"


def test_empty_special_columns_render_as_blank(qapp, svc, modern, undo):
    for kind in ("catalogues", "grades", "certifications", "links"):
        svc.show_special_block(modern, kind, show_in_table=True)
    svc.add_specimen(modern)

    model = _model(svc, modern, undo)
    for section in range(4, model.columnCount()):
        assert model.data(model.index(0, section), DISPLAY) == ""


def test_special_columns_survive_the_master_view(qapp, svc, modern, ancients, undo):
    svc.show_special_block(modern, "catalogues", display_label="References", show_in_table=True)
    krause = svc.create_catalog("KM", "Krause")
    coin = svc.add_specimen(modern)
    svc.add_reference(coin, krause, "1866", is_primary=True)
    svc.add_specimen(ancients)

    model = SpecimenTableModel(svc, undo)
    model.set_subcollection(None)
    assert model.rowCount() == 2
    headers = [
        model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())
    ]
    assert "References" in headers or "Catalogues" in headers
