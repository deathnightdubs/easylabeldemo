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

from numis.db import create_library
from numis.services import CollectionService
from numis.ui.commands import SetSortValue
from numis.ui.fields_dialog import ManageFieldsDialog, NewFieldDialog
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
        service.add_reference(specimen, krause, row[5], is_primary=True)

    service.reindex_all()
    window.session.commit()
    window._reload_subcollections(keep="Modern")
    return window


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
    model.sort(3, Qt.SortOrder.AscendingOrder)
    shot(app, window, target / "02-chinese-cash.png", 1080, 300)

    window.search_box.setText("通寶")
    window._search()
    shot(app, window, target / "03-cjk-search.png", 1080, 250)
    window.search_box.clear()
    window._search()

    # Confirm one guessed sort position, so the queue visibly shrinks.
    row = next(
        r for r in range(model.rowCount()) if model.data(model.index(r, 3)) == "Guangxu year 30"
    )
    window.undo.push(
        SetSortValue(window.service, model.specimen_at(row).id, model.field_at(3).id, 1904)
    )
    model.refresh()
    model.sort(3, Qt.SortOrder.AscendingOrder)
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

    new_field = NewFieldDialog(window.service, window)
    new_field.label_edit.setText("Fineness")
    new_field.type_combo.setCurrentText("Fineness")
    app.processEvents()
    shot(app, new_field, target / "08-new-column.png", 460, 220)
    new_field.close()

    del view
    window.session.commit()
    window.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
