#!/usr/bin/env python
"""Render screenshots of the interface, headlessly.

Run with Qt's offscreen platform so it needs no display:

    QT_QPA_PLATFORM=offscreen python tools/screenshots.py docs/images

Kept in the repository so the images in the documentation can be regenerated rather than
being stale artefacts nobody can reproduce.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from numis.columns import ColumnDisplay
from numis.db import create_library
from numis.filters import Criterion, FilterGroup, SortKey
from numis.services import CollectionService
from numis.ui.column_settings import ColumnSettingsDialog
from numis.ui.commands import SetSortValue
from numis.ui.fields_dialog import ManageFieldsDialog, NewFieldDialog
from numis.ui.filter_dialog import FilterDialog
from numis.ui.main_window import MASTER_VIEW, MainWindow

MODERN = [
    ("Maria Theresia", "1 Thaler", "1780", "28.07", "Austria", "KM#1866"),
    ("Victoria", "1 Crown", "1889", "28.28", "United Kingdom", "KM#765"),
    ("Victoria", "1 Crown", "1890", "28.28", "United Kingdom", "KM#765"),
    ("Wilhelm II", "5 Mark", "1913", "27.78", "Germany", "KM#523"),
    ("Franz Joseph I", "1 Corona", "1915", "5.00", "Austria", "KM#2820"),
]

CASH = [
    ("乾隆通寶 寶泉", "1 wen", "1736-1795", "4.10"),
    ("咸豐重寶 當十", "10 wen", "1851-1861", "10.50"),
    ("道光通寶", "1 wen", "1821-1850", "3.90"),
    ("光緒元寶", "half tael", "Guangxu year 30", "18.10"),
    ("嘉慶通寶", "1 wen", "1796-1820", "4.00"),
]

ANCIENTS = [
    ("Trajan", "1 Denarius", "c. 105", "3.20"),
    ("Hadrian", "1 Sestertius", "c. 125", "24.90"),
    ("Alexander III", "1 Tetradrachm", "336-323 BC", "17.20"),
]


def build(path: Path) -> MainWindow:
    shutil.rmtree(path, ignore_errors=True)
    window = MainWindow(create_library(path))
    service: CollectionService = window.service

    modern = service.create_subcollection(
        "Modern", naming_template="{country} {denomination}"
    )
    cash = service.create_subcollection(
        "Chinese Cash", naming_template="{legend}"
    )
    ancients = service.create_subcollection("Ancients", naming_template="{ruler} {denomination}")

    # One shared field, labelled differently in each subcollection.
    ruler = service.create_field("ruler", "Head of state", "text")
    denomination = service.create_field(
        "denomination", "Denomination", "text", config={"numeric_sort": True}
    )
    date = service.create_field("date_issued", "Date", "date")
    weight = service.create_field("weight", "Weight", "weight")
    country = service.create_field("country", "Country", "text")
    legend = service.create_field("legend", "Legend", "text")

    for order, field in enumerate((ruler, denomination, date, weight, country)):
        service.show_field(
            modern,
            field,
            display_label="Ruler" if field is ruler else None,
            show_in_table=True,
            sort_order=order,
        )
    for order, field in enumerate((legend, denomination, date, weight)):
        service.show_field(cash, field, show_in_table=True, sort_order=order)
    for order, field in enumerate((ruler, denomination, date, weight)):
        service.show_field(
            ancients,
            field,
            display_label="Emperor" if field is ruler else None,
            show_in_table=True,
            sort_order=order,
        )

    for subcollection, rows, keys in (
        (modern, MODERN, ("ruler", "denomination", "date_issued", "weight", "country")),
        (cash, CASH, ("legend", "denomination", "date_issued", "weight")),
        (ancients, ANCIENTS, ("ruler", "denomination", "date_issued", "weight")),
    ):
        for row in rows:
            service.add_specimen(subcollection, values=dict(zip(keys, row, strict=False)))

    # A catalogue reference or two, so the special systems are represented.
    krause = service.create_catalog("KM", "Standard Catalog of World Coins")
    for specimen, row in zip(
        service.session.scalars(service.live_specimens(modern)), MODERN, strict=False
    ):
        service.add_reference(specimen, krause, row[5], rank=1)

    service.reindex_all()
    window.session.commit()
    window._reload_subcollections(keep="Modern")
    return window


def column_index(model: object, label: str) -> int:
    """Find a column by its heading.

    Resolved by name rather than position on purpose: positions shift whenever the identity
    columns or a subcollection's fields change, and hardcoding them is exactly the bug that
    broke the grid when switching subcollections.
    """
    for section in range(model.columnCount()):
        if model.headerData(section, Qt.Orientation.Horizontal) == label:
            return section
    raise LookupError(f"no column headed {label!r}")


def shot(app: QApplication, widget: object, path: Path, width: int, height: int) -> None:
    widget.resize(width, height)
    widget.show()
    app.processEvents()
    if hasattr(widget, "view"):
        widget.view.resizeColumnsToContents()
    app.processEvents()
    widget.grab().save(str(path))
    print(f"  {path}")


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "docs/images")
    target.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    window = build(Path(".scratch/Screens.numis"))
    model, view = window.model, window.view

    print("writing screenshots:")

    window.subcollection_combo.setCurrentText("Modern")
    app.processEvents()
    shot(app, window, target / "01-modern.png", 1080, 300)

    window.subcollection_combo.setCurrentText("Chinese Cash")
    app.processEvents()
    date_column = column_index(model, "Date")
    model.sort(date_column, Qt.SortOrder.AscendingOrder)
    shot(app, window, target / "02-chinese-cash.png", 1080, 300)

    window.search_box.setText("通寶")
    window._search()
    shot(app, window, target / "03-cjk-search.png", 1080, 250)
    window.search_box.clear()
    window._search()

    # Confirm one guessed sort position, so the queue visibly shrinks.
    date_column = column_index(model, "Date")
    row = next(
        r
        for r in range(model.rowCount())
        if model.data(model.index(r, date_column)) == "Guangxu year 30"
    )
    window.undo.push(
        SetSortValue(
            window.service, model.specimen_at(row).id, model.field_at(date_column).id, 1904
        )
    )
    model.refresh()
    model.sort(column_index(model, "Date"), Qt.SortOrder.AscendingOrder)
    shot(app, window, target / "04-sort-values.png", 1080, 300)

    window.subcollection_combo.setCurrentText("Ancients")
    app.processEvents()
    shot(app, window, target / "05-ancients.png", 1080, 260)

    window.subcollection_combo.setCurrentText(MASTER_VIEW)
    app.processEvents()
    shot(app, window, target / "06-master-view.png", 1080, 400)

    window.subcollection_combo.setCurrentText("Modern")
    app.processEvents()
    columns = ManageFieldsDialog(window.service, window.current_subcollection(), window)
    shot(app, columns, target / "07-columns.png", 640, 380)
    columns.close()

    # The details panel, with the special systems populated.
    window.subcollection_combo.setCurrentText("Modern")
    app.processEvents()
    service = window.service
    scale = service.create_grade_scale("SHELDON", "Sheldon 1-70", kind="numeric")
    service.add_grade_level(scale, "MS63", 63.0, numeric_value=63.0)
    # A sticker reads by the name it was given, not by its issuer, so the short form and the
    # full name are both worth showing off here.
    service.create_grade_modifier(
        "CACG", "CAC Gold", "sticker", 0.15, abbreviation="CAC", issuer="CAC"
    )
    service.create_grade_modifier("FB", "Full Bands", "strike", 0.15, abbreviation="FB")
    ngc = service.create_grading_company("NGC", "Numismatic Guaranty Company")
    subcollection = window.current_subcollection()
    first = next(iter(service.session.scalars(service.live_specimens(subcollection))))
    service.add_grade(first, scale, "MS63", base_value=63.0,
                      modifiers=[("CACG", None), ("FB", None)],
                      source="tpg", assigned_by="NGC", rank=1)
    service.add_certification(first, ngc, cert_number="2871554-013", rank=1)
    service.add_link(first, "https://zeno.ru/showphoto.php?photo=12345", kind="zeno",
                     label="Zeno record")
    for kind in ("catalogues", "grades", "certifications", "links"):
        service.show_special_block(window.current_subcollection(), kind, show_in_table=True,
                                   sort_order=20)
    window.session.commit()
    window.model.refresh()
    window.view.selectRow(0)
    app.processEvents()
    shot(app, window, target / "09-details-panel.png", 1250, 420)

    # A sold coin, kept but italicised.
    window._set_status("sold")
    window.disposed_action.setChecked(True)
    app.processEvents()
    shot(app, window, target / "10-sold.png", 1250, 300)
    window.undo.undo()

    new_field = NewFieldDialog(window.service, window)
    new_field.label_edit.setText("Fineness")
    new_field.type_combo.setCurrentText("Fineness")
    app.processEvents()
    shot(app, new_field, target / "08-new-column.png", 460, 220)
    new_field.close()

    # Per-column display settings: what a grade column shows of each grade.
    settings = ColumnSettingsDialog(
        service, window.current_subcollection(), "grades", ColumnDisplay(), window
    )
    settings.modifier_details.setChecked(True)
    settings.modifier_full_names.setChecked(True)
    settings.show_assigned_by.setChecked(True)
    app.processEvents()
    shot(app, settings, target / "11-column-settings.png", 560, 460)
    settings.close()

    # Building a filter, with a nested group for a question a flat list cannot ask.
    window.subcollection_combo.setCurrentText("Modern")
    app.processEvents()
    current = FilterGroup.of(Criterion("field:weight", "gte", ("20",)))
    filters = FilterDialog(
        service, window.current_subcollection(), window.model.column_labels(), current, window
    )
    filters.group_box.setChecked(True)
    for row, ruler in enumerate(("Victoria", "Maria Theresia")):
        if row >= filters.subgroup.table.rowCount():
            filters.subgroup.add_row()
        table = filters.subgroup.table
        column, operator = table.cellWidget(row, 0), table.cellWidget(row, 1)
        column.setCurrentIndex(column.findData("field:ruler"))
        operator.setCurrentIndex(operator.findData("is"))
        table.cellWidget(row, 2).setText(ruler)
    filters._update()
    app.processEvents()
    shot(app, filters, target / "12-filter.png", 720, 560)

    # The result in the grid, sorted by two columns.
    window.model.set_filters(filters.group())
    window.model.set_sort_keys(
        [SortKey("field:ruler"), SortKey("field:date_issued", descending=True)]
    )
    window.view.refresh_sort_indicator()
    window._show_filter_state()
    filters.close()
    app.processEvents()
    shot(app, window, target / "13-filtered.png", 1250, 300)
    window.model.set_filters(None)
    window.model.set_sort_keys([])

    del view
    window.session.commit()
    window.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
