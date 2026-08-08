"""Measurement and money parsing into canonical units."""

from __future__ import annotations

import pytest

from numis.errors import FieldParseError
from numis.fields import units


@pytest.mark.parametrize(
    ("raw", "grams"),
    [("27.15", 27.15), ("27.15 g", 27.15), ("420 gr", 27.2155), ("1 ozt", 31.1035),
     ("1000mg", 1.0), ("1 dwt", 1.5552)],
)
def test_mass_to_grams(raw, grams):
    assert units.parse_mass(raw) == pytest.approx(grams, abs=1e-4)


@pytest.mark.parametrize(
    ("raw", "mm"),
    [("38.1", 38.1), ("38.1mm", 38.1), ("3.81cm", 38.1), ("1.5 in", 38.1),
     ("1 1/2 in", 38.1), ('1.5"', 38.1)],
)
def test_length_to_mm(raw, mm):
    assert units.parse_length(raw) == pytest.approx(mm, abs=1e-6)


@pytest.mark.parametrize(
    ("raw", "per_mille"),
    [("0.900", 900.0), ("900", 900.0), ("90%", 900.0), ("22K", 916.667),
     ("92.5", 925.0), (".925", 925.0), ("999", 999.0), ("24kt", 1000.0)],
)
def test_purity_forms_all_agree(raw, per_mille):
    """The same fineness written four ways stores as one value."""
    assert units.parse_purity(raw) == pytest.approx(per_mille, abs=1e-3)


def test_purity_rejects_impossible_values():
    with pytest.raises(FieldParseError):
        units.parse_purity("1200")
    with pytest.raises(FieldParseError):
        units.parse_purity("30K")


@pytest.mark.parametrize(
    ("raw", "degrees"), [("180", 180.0), ("180°", 180.0), ("6h", 180.0), ("12h", 0.0),
                         ("3 o'clock", 90.0), ("370", 10.0)]
)
def test_angle_including_clock_notation(raw, degrees):
    assert units.parse_angle(raw) == pytest.approx(degrees)


@pytest.mark.parametrize(
    ("raw", "minor"),
    [("12.50", 1250), ("$1,234.56", 123456), ("1234.56", 123456), ("0.01", 1),
     ("1.005", 101), ("1.234,56", 123456), ("-5.00", -500)],
)
def test_money_is_exact_integer_minor_units(raw, minor):
    """Money never goes through a float: a lifetime of transactions must stay exact."""
    assert units.parse_money(raw, decimals=2) == minor


def test_money_respects_zero_decimal_currencies():
    assert units.parse_money("1500", decimals=0) == 1500


def test_formatting_round_trips_display_units():
    assert units.format_mass(31.1034768, "ozt", 3) == "1.000 ozt"
    assert units.format_length(25.4, "in", 2) == "1.00 in"
    assert units.format_purity(900.0, "percent") == "90.0%"
    assert units.format_purity(916.667, "karat") == "22K"  # a whole karat drops the '.0'
    assert units.format_purity(937.5, "karat") == "22.5K"
    assert units.format_angle(180.0, "clock") == "6h"
    assert units.format_money(123456, 2, "$") == "$1,234.56"


def test_fractions_are_accepted_like_the_label_generator():
    assert units.parse_number("1 1/4") == pytest.approx(1.25)
    assert units.parse_number("-1/16") == pytest.approx(-0.0625)


def test_unknown_unit_is_refused_with_a_reason():
    with pytest.raises(FieldParseError) as info:
        units.parse_mass("5 stone")
    assert "unknown mass unit" in str(info.value)
