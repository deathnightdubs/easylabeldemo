"""The field system: types, values, sort keys, and safe schema changes."""

from __future__ import annotations

import pytest

from numis.errors import FieldParseError, NumisError, UnknownFieldType
from numis.fields import FIELD_TYPE_KEYS, get_field_type, parse_value


def test_no_lookup_or_grade_field_types():
    """Both were dropped by design: remembered vocabularies, and grading as a plain field."""
    assert "lookup" not in FIELD_TYPE_KEYS
    assert "grade" not in FIELD_TYPE_KEYS


def test_unknown_type_names_the_alternatives():
    with pytest.raises(UnknownFieldType) as info:
        get_field_type("banana")
    assert "available" in str(info.value)


def test_text_fields_do_not_ask_for_sort_values_by_default():
    """Ordinary text columns must never be flagged, or the flag becomes noise."""
    columns = parse_value("text", "Austria")
    assert columns["sort_value"] is None
    assert columns["needs_review"] == 0


@pytest.mark.parametrize(
    ("raw", "sort_value", "review"),
    [("1 wen", 1.0, 0), ("10 wen", 10.0, 0), ("100 cash", 100.0, 0), ("half tael", None, 1)],
)
def test_numeric_sort_is_opt_in_per_field(raw, sort_value, review):
    columns = parse_value("text", raw, {"numeric_sort": True})
    assert columns["sort_value"] == sort_value
    assert columns["needs_review"] == review


def test_approximate_values_are_recorded_as_such():
    columns = parse_value("weight", "~27.1")
    assert columns["is_approximate"] == 1
    assert columns["value"] == pytest.approx(27.1)


def test_entered_as_keeps_the_original_expression_only_when_it_differs():
    assert parse_value("purity", "22K")["entered_as"] == "22K"
    assert parse_value("purity", "900")["entered_as"] is None


def test_config_bounds_are_enforced():
    with pytest.raises(FieldParseError):
        parse_value("number", "150", {"max": 100})


def test_rating_rejects_half_stars_unless_enabled():
    with pytest.raises(FieldParseError):
        parse_value("rating", "3.5")
    assert parse_value("rating", "3.5", {"allow_half": True})["value"] == 3.5


class TestValues:
    def test_set_and_read_back(self, svc, modern):
        field = svc.create_field("weight", "Weight", "weight")
        svc.show_field(modern, field)
        coin = svc.add_specimen(modern, values={"weight": "27.15 g"})
        assert svc.get_value(coin, field).value == pytest.approx(27.15)
        assert svc.display(coin, field) == "27.15 g"

    def test_display_unit_changes_nothing_stored(self, svc, modern):
        field = svc.create_field("diameter", "Diameter", "dimension", config={"display_unit": "in"})
        coin = svc.add_specimen(modern, values={"diameter": "38.1mm"})
        assert svc.get_value(coin, field).value == pytest.approx(38.1)
        assert svc.display(coin, field) == "1.50 in"

    def test_money_uses_library_currency_settings(self, svc, modern):
        field = svc.create_field("price", "Price paid", "money")
        coin = svc.add_specimen(modern, values={"price": "125.00"})
        assert svc.get_value(coin, field).amount_minor == 12500
        assert svc.display(coin, field) == "$125.00"

    def test_setting_a_value_twice_replaces_it(self, svc, modern):
        field = svc.create_field("note", "Note", "text")
        coin = svc.add_specimen(modern, values={"note": "first"})
        svc.set_value(coin, field, "second")
        assert svc.display(coin, field) == "second"

    def test_multi_value_requires_the_field_to_allow_it(self, svc, modern):
        single = svc.create_field("one", "One", "text")
        coin = svc.add_specimen(modern)
        svc.set_value(coin, single, "a")
        with pytest.raises(NumisError):
            svc.set_value(coin, single, "b", seq=1)

    def test_multi_value_field_keeps_both(self, svc, modern):
        multi = svc.create_field("many", "Many", "text", is_multi=True)
        coin = svc.add_specimen(modern)
        svc.set_value(coin, multi, "a")
        svc.set_value(coin, multi, "b", seq=1)
        assert svc.display(coin, multi, seq=0) == "a"
        assert svc.display(coin, multi, seq=1) == "b"

    def test_unset_value_displays_as_empty_not_an_error(self, svc, modern):
        field = svc.create_field("mint", "Mint", "text")
        coin = svc.add_specimen(modern)
        assert svc.display(coin, field) == ""


class TestSortKeys:
    def test_manual_override_wins_and_clears_review(self, svc, modern):
        field = svc.create_field("date_issued", "Date", "date")
        coin = svc.add_specimen(modern, values={"date_issued": "Qianlong year 22"})
        assert svc.get_value(coin, field).needs_review == 1

        svc.set_sort_value(coin, field, 1757)
        row = svc.get_value(coin, field)
        assert (row.sort_value, row.sort_source, row.needs_review) == (1757.0, "manual", 0)
        assert row.display == "Qianlong year 22"

    def test_review_queue_lists_only_what_needs_confirming(self, svc, modern):
        svc.create_field("date_issued", "Date", "date")
        svc.add_specimen(modern, values={"date_issued": "1943"})
        svc.add_specimen(modern, values={"date_issued": "1736-1795"})
        svc.add_specimen(modern, values={"date_issued": "undated"})
        queue = svc.needs_review()
        assert [entry[2] for entry in queue] == ["1736-1795"]

    def test_sorting_by_date_uses_the_numeric_key(self, svc, modern):
        svc.create_field("date_issued", "Date", "date")
        for raw in ["1943", "c. 350 BC", "1736-1795", "undated"]:
            svc.add_specimen(modern, values={"date_issued": raw})
        ordered = svc.sorted_by_field("date_issued")
        assert [svc.display(c, "date_issued") for c in ordered] == [
            "c. 350 BC", "1736-1795", "1943", "undated",
        ]

    def test_missing_values_sort_last_in_both_directions(self, svc, modern):
        field = svc.create_field("weight", "Weight", "weight")
        light = svc.add_specimen(modern, values={"weight": "5"})
        heavy = svc.add_specimen(modern, values={"weight": "50"})
        blank = svc.add_specimen(modern)
        assert svc.sorted_by_field(field) == [light, heavy, blank]
        assert svc.sorted_by_field(field, descending=True) == [heavy, light, blank]

    def test_denominations_order_by_their_sort_values(self, svc, modern):
        field = svc.create_field("denom", "Denomination", "text", config={"numeric_sort": True})
        coins = {}
        for raw in ["1 wen", "10 wen", "100 cash", "half tael", "1 mace"]:
            coins[raw] = svc.add_specimen(modern, values={"denom": raw})
        svc.set_sort_value(coins["1 mace"], field, 1000)
        svc.set_sort_value(coins["half tael"], field, 18650)
        ordered = [svc.display(c, field) for c in svc.sorted_by_field(field)]
        assert ordered == ["1 wen", "10 wen", "100 cash", "1 mace", "half tael"]


class TestSchemaChanges:
    def test_hiding_a_field_keeps_its_values(self, svc, modern):
        field = svc.create_field("mint", "Mint", "text")
        svc.show_field(modern, field, show_in_table=True)
        coin = svc.add_specimen(modern, values={"mint": "Vienna"})

        svc.hide_field(modern, field)
        assert svc.columns_for(modern) == []
        assert svc.display(coin, field) == "Vienna"

        svc.show_field(modern, field, show_in_table=True)
        assert [c.key for c in svc.columns_for(modern)] == ["mint"]

    def test_archiving_is_reversible(self, svc, modern):
        field = svc.create_field("mint", "Mint", "text")
        svc.show_field(modern, field, show_in_table=True)
        coin = svc.add_specimen(modern, values={"mint": "Vienna"})

        svc.archive_field(field)
        assert svc.columns_for(modern) == []
        svc.restore_field(field)
        assert svc.display(coin, field) == "Vienna"

    def test_purge_reports_how_many_values_it_destroys(self, svc, modern):
        field = svc.create_field("mint", "Mint", "text")
        for name in ("Vienna", "Kremnica", "Prague"):
            svc.add_specimen(modern, values={"mint": name})
        assert svc.count_values(field) == 3
        assert svc.purge_field(field) == 3

    def test_type_change_adds_converts_and_archives(self, svc, modern):
        """Nothing is mutated in place, so the operation is reversible."""
        field = svc.create_field("year", "Year", "text")
        good = svc.add_specimen(modern, values={"year": "1943"})
        bad = svc.add_specimen(modern, values={"year": "Qianlong year 22"})

        replacement, problems = svc.convert_field_type(field, "date", new_key="year_date")
        assert field.is_archived == 1
        assert replacement.data_type == "date"
        assert svc.display(good, replacement) == "1943"
        assert svc.get_value(good, replacement).sort_value == 1943.0
        # The unreadable one still converts: dates keep unparseable text and flag it.
        assert problems == []
        assert svc.get_value(bad, replacement).needs_review == 1
        # And the original values are still there, in the archived field.
        assert svc.display(good, field) == "1943"

    def test_failed_conversions_are_reported_not_silently_dropped(self, svc, modern):
        field = svc.create_field("note", "Note", "text")
        svc.add_specimen(modern, values={"note": "27.15"})
        svc.add_specimen(modern, values={"note": "not a number"})

        replacement, problems = svc.convert_field_type(field, "weight", new_key="note_weight")
        assert len(problems) == 1
        assert "not a number" in problems[0].message
