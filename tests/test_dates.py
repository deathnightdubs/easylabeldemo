"""Coin dates: the display-plus-numeric approach from docs/design/01, Part 4.2."""

from __future__ import annotations

import pytest

from numis.fields import parse_coin_date, set_manual_sort_value


@pytest.mark.parametrize(
    ("raw", "sort_value", "source", "precision", "review"),
    [
        # The table in the specification.
        ("1943", 1943.0, "auto", "exact_year", False),
        ("1736-1795", 1765.5, "auto", "range", True),
        ("c. 350 BC", -350.0, "auto", "circa", False),
        ("AH 1256", 1840.6, "auto", "exact_year", True),
        ("Qianlong year 22", None, "none", "unknown", True),
        ("undated", None, "none", "unknown", False),
        ("1804", 1804.0, "auto", "exact_year", False),
    ],
)
def test_specification_examples(raw, sort_value, source, precision, review):
    parsed = parse_coin_date(raw)
    assert parsed.sort_value == sort_value
    assert parsed.sort_source == source
    assert parsed.precision == precision
    assert parsed.needs_review is review


def test_display_is_never_rewritten():
    """The user's own expression survives parsing untouched."""
    for raw in ("c. 350 BC", "Qianlong year 22", "1736-1795", "  1943  "):
        assert parse_coin_date(raw).display == raw.strip()


@pytest.mark.parametrize(
    ("raw", "start", "end", "precision"),
    [
        ("1943-05-01", 1943, 1943, "exact_day"),
        ("1943-05", 1943, 1943, "exact_month"),
        ("1930s", 1930, 1939, "decade"),
        ("18th century", 1701, 1800, "century"),
        ("3rd century BC", -300, -201, "century"),
        ("350-320 BC", -350, -320, "range"),
        ("50 BC - 20 AD", -50, 20, "range"),
        ("1804 to 1807", 1804, 1807, "range"),
        ("-44", -44, -44, "exact_year"),
        ("c. 1850", 1850, 1850, "circa"),
    ],
)
def test_span_forms(raw, start, end, precision):
    parsed = parse_coin_date(raw)
    assert (parsed.year_start, parsed.year_end) == (start, end)
    assert parsed.precision == precision


def test_bc_uses_historical_convention_with_no_year_zero():
    assert parse_coin_date("1 BC").year_start == -1
    assert parse_coin_date("1 AD").year_start == 1


def test_hijri_is_flagged_and_keeps_the_era_label():
    parsed = parse_coin_date("AH 1256")
    assert parsed.calendar == "islamic_ah"
    assert parsed.era_label == "AH 1256"
    assert parsed.needs_review is True
    assert "1840.6" in parsed.note


def test_range_explains_the_midpoint_it_chose():
    parsed = parse_coin_date("1736-1795")
    assert "midpoint" in parsed.note
    assert "1736" in parsed.note and "1795" in parsed.note


def test_single_years_are_not_flagged():
    """A plain year needs no confirmation, so entry is not interrupted."""
    assert parse_coin_date("1943").needs_review is False
    assert parse_coin_date("1943").note is None


def test_unreadable_input_is_kept_and_asks_for_a_number():
    parsed = parse_coin_date("Qianlong year 22")
    assert parsed.display == "Qianlong year 22"
    assert parsed.sort_value is None
    assert parsed.needs_review is True
    assert "sort at" in parsed.note


def test_manual_sort_value_clears_the_flag():
    parsed = set_manual_sort_value(parse_coin_date("Qianlong year 22"), 1757)
    assert parsed.sort_value == 1757.0
    assert parsed.sort_source == "manual"
    assert parsed.needs_review is False
    assert parsed.display == "Qianlong year 22"


def test_unknown_dates_sort_last():
    dates = ["1943", "undated", "c. 350 BC", "1736-1795"]
    parsed = [parse_coin_date(raw) for raw in dates]
    ordered = sorted(parsed, key=lambda p: (p.sort_value is None, p.sort_value or 0))
    assert [p.display for p in ordered] == ["c. 350 BC", "1736-1795", "1943", "undated"]


def test_as_columns_matches_the_table():
    columns = parse_coin_date("1736-1795").as_columns()
    assert columns["needs_review"] == 1  # stored as an integer
    assert "note" not in columns  # advisory only, not a column
    assert columns["display"] == "1736-1795"
