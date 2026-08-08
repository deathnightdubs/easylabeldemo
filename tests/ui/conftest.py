"""Fixtures for interface tests.

Qt runs on its ``offscreen`` platform so these tests need no display and can run in CI. The
environment variable must be set before Qt is imported, which is why it happens here rather
than in a fixture body.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# The core library works without a GUI toolkit installed, so these tests skip rather than
# fail when it is absent.
pytest.importorskip("PySide6", reason="install the 'gui' extra to run interface tests")

from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication

from numis.services import CollectionService
from numis.ui.sheet_view import SheetView
from numis.ui.table_model import SpecimenTableModel


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """One application for the whole run; Qt allows only one."""
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def undo(qapp: QApplication) -> QUndoStack:
    return QUndoStack()


@pytest.fixture
def sheet(qapp, svc: CollectionService, undo: QUndoStack, modern):
    """A table model and view over a subcollection with a few typical columns."""
    fields = [
        ("ruler", "Ruler", "text", {}),
        ("denom", "Denomination", "text", {"numeric_sort": True}),
        ("date_issued", "Date", "date", {}),
        ("weight", "Weight", "weight", {}),
    ]
    for order, (key, label, data_type, config) in enumerate(fields):
        field = svc.create_field(key, label, data_type, config=config)
        svc.show_field(modern, field, show_in_table=True, sort_order=order)

    model = SpecimenTableModel(svc, undo)
    view = SheetView()
    view.setModel(model)
    model.set_subcollection(modern)
    return model, view
