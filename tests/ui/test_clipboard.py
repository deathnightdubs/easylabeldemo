"""Clipboard handling, which is what makes the grid interoperate with a spreadsheet."""

from __future__ import annotations

from numis.ui import clipboard


def test_encode_uses_tabs_and_newlines_like_a_spreadsheet():
    assert clipboard.encode([["a", "b"], ["c", "d"]]) == "a\tb\nc\td"


def test_encode_neutralises_tabs_and_newlines_inside_values():
    """Otherwise one value containing a tab would silently shift the whole grid."""
    assert clipboard.encode([["a\tb", "c\nd"]]) == "a b\tc d"


def test_decode_handles_every_platforms_line_endings():
    for text in ("a\tb\nc\td", "a\tb\r\nc\td", "a\tb\rc\td"):
        assert clipboard.decode(text) == [["a", "b"], ["c", "d"]]


def test_decode_ignores_the_trailing_newline_spreadsheets_add():
    assert clipboard.decode("a\tb\n") == [["a", "b"]]


def test_decode_of_nothing_is_empty():
    assert clipboard.decode("") == []
    assert clipboard.decode("\n") == []


def test_shape_uses_the_widest_row():
    assert clipboard.shape([["a"], ["b", "c"]]) == (2, 2)
    assert clipboard.shape([]) == (0, 0)


def test_a_single_value_fills_a_selection():
    assert clipboard.tile([["x"]], 2, 3) == [["x", "x", "x"], ["x", "x", "x"]]


def test_a_column_repeats_sideways():
    assert clipboard.tile([["a"], ["b"]], 2, 2) == [["a", "a"], ["b", "b"]]


def test_a_block_repeats_to_fill():
    assert clipboard.tile([["a", "b"]], 3, 2) == [["a", "b"], ["a", "b"], ["a", "b"]]
