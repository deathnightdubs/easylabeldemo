"""Application entry point.

    python -m numis.ui                    open the last library, or ask
    python -m numis.ui path/to/Lib.numis  open a specific library
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ..db import Library, create_library, open_library
from .main_window import MainWindow


def _choose_library(parent: object | None = None) -> Library | None:
    """Ask for a library folder, offering to create one if it is empty."""
    path = QFileDialog.getExistingDirectory(None, "Choose or create a library folder")
    if not path:
        return None
    folder = Path(path)
    if (folder / "collection.db").is_file():
        return open_library(folder)
    return create_library(folder, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="numis-gui", description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="library folder")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Collection")

    try:
        if args.path is not None:
            library = (
                open_library(args.path)
                if (args.path / "collection.db").is_file()
                else create_library(args.path, exist_ok=True)
            )
        else:
            library = _choose_library()
    except Exception as exc:
        QMessageBox.critical(None, "Library", str(exc))
        return 1

    if library is None:
        return 0

    window = MainWindow(library)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
