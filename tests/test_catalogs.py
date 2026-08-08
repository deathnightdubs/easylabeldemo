"""Catalogue numbers: ordering, ranges, duplicates and combined columns."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from numis.catalogs import normalise, parse_number, range_bounds, sort_key


def test_specification_ordering():
    """The exact order given in docs/design/02, Part 4.1."""
    numbers = ["2", "2.1", "10", "54", "A54", "A54.2", "54.2", "1042", "1042a", "22.123",
               "22.9", "B54"]
    assert sorted(numbers, key=sort_key) == [
        "2", "2.1", "10", "22.9", "22.123", "54", "54.2", "A54", "A54.2", "B54", "1042", "1042a",
    ]


def test_numbers_sort_numerically_not_as_text():
    assert sort_key("2") < sort_key("10")
    assert sorted(["10", "2", "1042"], key=sort_key) == ["2", "10", "1042"]


def test_letter_prefixed_variants_sit_beside_their_base_number():
    """A54 is a variant of 54, not an entry in the fifty-thousands."""
    ordered = sorted(["54", "A54", "55"], key=sort_key)
    assert ordered == ["54", "A54", "55"]


def test_letter_prefix_order_is_configurable_per_catalogue():
    numbers = ["54", "A54", "54.2"]
    assert sorted(numbers, key=lambda n: sort_key(n, letter_prefix_order="after")) == [
        "54", "54.2", "A54",
    ]
    assert sorted(numbers, key=lambda n: sort_key(n, letter_prefix_order="before")) == [
        "A54", "54", "54.2",
    ]


def test_sub_numbers_follow_their_parent():
    assert sort_key("1042") < sort_key("1042a")
    assert sort_key("54") < sort_key("54.2")


def test_catalogue_code_is_stripped_so_formatting_does_not_matter():
    assert sort_key("KM#2", catalog_code="KM") == sort_key("2")
    assert normalise("KM#2.1", "KM") == normalise("km 2.1", "KM") == normalise("2.1", "KM")


def test_parse_number_extracts_prefix_base_and_segments():
    parsed = parse_number("KM#A54.2", catalog_code="KM")
    assert (parsed.prefix, parsed.base, parsed.segments) == ("a", 54, ("00000002",))
    assert parsed.raw == "A54.2"


def test_range_includes_sub_numbers_of_the_endpoint():
    low, high = range_bounds("22", "54")
    inside = [n for n in ["22.9", "54", "54.2", "55"] if low <= sort_key(n) <= high]
    assert inside == ["22.9", "54", "54.2"]


def test_range_matches_the_specification_example():
    low, high = range_bounds("10", "54.2")
    numbers = ["2", "10", "22.9", "22.123", "54", "54.2", "1042"]
    assert [n for n in numbers if low <= sort_key(n) <= high] == [
        "10", "22.9", "22.123", "54", "54.2",
    ]


class TestReferences:
    def test_reference_is_stored_three_ways(self, svc, modern):
        km = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern)
        reference = svc.add_reference(coin, km, "KM#A54.2")
        assert reference.number_raw == "A54.2"
        assert reference.number_norm == "A54.2"
        assert reference.sort_segments.startswith("00000054|a")

    def test_one_coin_may_carry_several_catalogues(self, svc, modern):
        km = svc.create_catalog("KM", "Krause")
        hartill = svc.create_catalog("H", "Hartill")
        coin = svc.add_specimen(modern)
        svc.add_reference(coin, km, "C1-3")
        svc.add_reference(coin, hartill, "22.123")
        assert len(svc.references_for(coin)) == 2
        assert len(svc.references_for(coin, catalog=hartill)) == 1

    def test_combined_cell_does_not_repeat_the_catalogue_code(self, svc, modern):
        km = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern)
        svc.add_reference(coin, km, "KM#2073")
        assert svc.combined_catalogue_cell(coin) == "KM 2073"

    def test_the_same_number_cannot_be_recorded_twice_however_it_is_typed(
        self, svc, modern, session
    ):
        km = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern)
        svc.add_reference(coin, km, "KM#2073")
        with pytest.raises(IntegrityError):
            svc.add_reference(coin, km, "2073")
            session.flush()

    def test_only_one_primary_reference_per_coin(self, svc, modern):
        km = svc.create_catalog("KM", "Krause")
        hartill = svc.create_catalog("H", "Hartill")
        coin = svc.add_specimen(modern)
        first = svc.add_reference(coin, km, "2073", is_primary=True)
        second = svc.add_reference(coin, hartill, "22.123", is_primary=True)
        assert first.is_primary == 0
        assert second.is_primary == 1

    def test_sorting_by_a_catalogue_puts_coins_without_a_number_last(self, svc, modern):
        """The answered decision: blanks go to the bottom, in both directions."""
        km = svc.create_catalog("KM", "Krause")
        low = svc.add_specimen(modern, display_name="low")
        high = svc.add_specimen(modern, display_name="high")
        svc.add_specimen(modern, display_name="none")  # no reference in this catalogue
        svc.add_reference(low, km, "10")
        svc.add_reference(high, km, "1042")

        assert [c.display_name for c in svc.sorted_by_catalogue(km)] == ["low", "high", "none"]
        assert [c.display_name for c in svc.sorted_by_catalogue(km, descending=True)] == [
            "high", "low", "none",
        ]

    def test_a_combined_column_is_still_sortable_by_one_catalogue(self, svc, modern):
        """Both display modes are supported, and sorting works in the combined one."""
        km = svc.create_catalog("KM", "Krause")
        hartill = svc.create_catalog("H", "Hartill")
        first = svc.add_specimen(modern, display_name="first")
        second = svc.add_specimen(modern, display_name="second")
        # Hartill order is the opposite of KM order, so the chosen catalogue really decides.
        svc.add_reference(first, km, "10")
        svc.add_reference(first, hartill, "900")
        svc.add_reference(second, km, "20")
        svc.add_reference(second, hartill, "100")

        assert [c.display_name for c in svc.sorted_by_catalogue(km)] == ["first", "second"]
        assert [c.display_name for c in svc.sorted_by_catalogue(hartill)] == ["second", "first"]

    def test_range_query_finds_specimens(self, svc, modern):
        hartill = svc.create_catalog("H", "Hartill")
        inside = svc.add_specimen(modern, display_name="inside")
        outside = svc.add_specimen(modern, display_name="outside")
        svc.add_reference(inside, hartill, "22.150")
        svc.add_reference(outside, hartill, "23.001")
        found = svc.specimens_in_catalogue_range(hartill, "22.100", "22.199")
        assert [c.display_name for c in found] == ["inside"]
