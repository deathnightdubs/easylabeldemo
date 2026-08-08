"""Tab-separated clipboard handling.

Kept free of Qt so it can be tested directly. Tab-separated text is what spreadsheets put on
the clipboard, so blocks of cells copy and paste between this program and Excel without any
special export step.
"""

from __future__ import annotations

from collections.abc import Sequence


def encode(rows: Sequence[Sequence[str]]) -> str:
    """Render a block of cells as spreadsheet-compatible text.

    Tabs and newlines inside a value would corrupt the grid, so they are replaced by spaces.
    """
    return "\n".join(
        "\t".join(str(cell).replace("\t", " ").replace("\n", " ") for cell in row) for row in rows
    )


def decode(text: str) -> list[list[str]]:
    """Parse clipboard text into a block of cells.

    Accepts the line endings every platform produces, and drops a single trailing newline
    because spreadsheets add one when copying whole rows.
    """
    if not text:
        return []
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalised.endswith("\n"):
        normalised = normalised[:-1]
    if not normalised:
        return []
    return [line.split("\t") for line in normalised.split("\n")]


def shape(block: Sequence[Sequence[str]]) -> tuple[int, int]:
    """Rows and columns of a block, using the widest row."""
    if not block:
        return (0, 0)
    return (len(block), max(len(row) for row in block))


def tile(block: Sequence[Sequence[str]], rows: int, columns: int) -> list[list[str]]:
    """Repeat a block to fill a larger selection, as spreadsheets do.

    Pasting one cell across a selection fills it; pasting a column repeats it sideways. Any
    remainder is truncated rather than partially applied.
    """
    if not block:
        return []
    source_rows, source_columns = shape(block)
    out: list[list[str]] = []
    for row in range(rows):
        source = block[row % source_rows]
        out.append([source[column % max(len(source), 1)] for column in range(columns)])
    return out
