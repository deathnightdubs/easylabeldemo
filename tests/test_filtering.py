"""Filtering, searching and multi-column sorting against a real database.

The tests in ``test_filters.py`` cover the shape of a filter; these cover what it actually
returns, which is where an EXISTS subquery either behaves or quietly matches the wrong coins.
"""

from __future__ import annotations

import pytest

from numis.errors import NumisError
from numis.filters import Criterion, FilterGroup, SortKey


@pytest.fixture
def collection(svc, modern):
    """Four coins with enough variety to tell correct filtering from lucky filtering."""
    svc.create_field("ruler", "Ruler", "text")
    svc.create_field("metal", "Metal", "text")
    svc.create_field("weight", "Weight", "weight")
    svc.create_field("issued", "Issued", "date")
    svc.create_field("notes", "Notes", "long_text")
    svc.create_field("holed", "Holed", "boolean")

    coins = {}
    coins["qianlong"] = svc.add_specimen(
        modern,
        display_name="Qianlong cash",
        inventory_code="1",
        values={
            "ruler": "Qianlong", "metal": "bronze", "weight": "4.2 g",
            "issued": "1736-1795", "notes": "A pleasant example", "holed": "yes",
        },
    )
    coins["jiaqing"] = svc.add_specimen(
        modern,
        display_name="Jiaqing cash",
        inventory_code="2",
        values={
            "ruler": "Jiaqing", "metal": "bronze", "weight": "3.1 g",
            "issued": "1796-1820", "holed": "yes",
        },
    )
    coins["victoria"] = svc.add_specimen(
        modern,
        display_name="Victoria penny",
        inventory_code="10",
        values={
            "ruler": "Victoria", "metal": "copper", "weight": "9.4 g",
            "issued": "1875", "notes": "Bought at a show", "holed": "no",
        },
    )
    coins["blank"] = svc.add_specimen(modern, display_name="Unidentified", inventory_code="A7")
    return coins


def matched(svc, group, **kwargs) -> set[str]:
    """The names of the coins a filter returns."""
    return {
        specimen.display_name
        for specimen in svc.query_specimens(filters=group, **kwargs)
    }


def one(target, operator, *values) -> FilterGroup:
    return FilterGroup.of(Criterion(target, operator, tuple(values)))


class TestTextFields:
    def test_contains_is_case_insensitive(self, svc, collection):
        assert matched(svc, one("field:ruler", "contains", "qian")) == {"Qianlong cash"}

    def test_is_matches_exactly_but_ignores_capitals(self, svc, collection):
        assert matched(svc, one("field:ruler", "is", "victoria")) == {"Victoria penny"}

    def test_starts_with(self, svc, collection):
        assert matched(svc, one("field:ruler", "starts_with", "Ji")) == {"Jiaqing cash"}

    def test_ends_with(self, svc, collection):
        assert matched(svc, one("field:ruler", "ends_with", "long")) == {"Qianlong cash"}

    def test_is_not_finds_the_coins_recording_something_else(self, svc, collection):
        assert matched(svc, one("field:metal", "is_not", "bronze")) == {"Victoria penny"}

    def test_does_not_contain(self, svc, collection):
        found = matched(svc, one("field:ruler", "not_contains", "ing"))
        assert found == {"Qianlong cash", "Victoria penny"}

    def test_a_negative_filter_leaves_out_the_coins_with_nothing_recorded(self, svc, collection):
        """A coin with no metal recorded makes no claim about its metal.

        Counting it as "not bronze" would fill a narrowing filter with blank rows, which is the
        opposite of what the filter is for.
        """
        assert "Unidentified" not in matched(svc, one("field:metal", "is_not", "bronze"))
        assert "Unidentified" not in matched(svc, one("field:metal", "not_contains", "bronze"))

    def test_wanting_the_blanks_too_is_expressible(self, svc, collection):
        """The escape hatch, and it reads as what it is."""
        group = FilterGroup.of(
            Criterion("field:metal", "empty"),
            Criterion("field:metal", "is_not", ("bronze",)),
            match="any",
        )
        assert matched(svc, group) == {"Victoria penny", "Unidentified"}

    def test_empty_finds_the_coins_missing_it(self, svc, collection):
        assert matched(svc, one("field:ruler", "empty")) == {"Unidentified"}

    def test_not_empty_finds_the_rest(self, svc, collection):
        assert matched(svc, one("field:ruler", "not_empty")) == {
            "Qianlong cash", "Jiaqing cash", "Victoria penny",
        }

    def test_a_long_text_field_can_be_searched_by_substring(self, svc, collection):
        assert matched(svc, one("field:notes", "contains", "show")) == {"Victoria penny"}


class TestMultiValuedFields:
    """A coin holding two rulers must not match its own exclusion."""

    @pytest.fixture
    def two_rulers(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text", is_multi=True)
        coin = svc.add_specimen(modern, display_name="Joint issue")
        svc.set_value(coin, "ruler", "Victoria", seq=0)
        svc.set_value(coin, "ruler", "Albert", seq=1)
        other = svc.add_specimen(modern, display_name="Victoria only")
        svc.set_value(other, "ruler", "Victoria")
        return coin, other

    def test_any_value_matching_is_enough(self, svc, two_rulers):
        assert matched(svc, one("field:ruler", "is", "Albert")) == {"Joint issue"}

    def test_is_not_means_no_value_is_that(self, svc, two_rulers):
        found = matched(svc, one("field:ruler", "is_not", "Victoria"))
        assert found == set(), "both coins have a Victoria value, so neither is excluded"

    def test_a_coin_is_returned_once_however_many_values_it_has(self, svc, two_rulers):
        found = svc.query_specimens(filters=one("field:ruler", "not_empty"))
        assert len(found) == 2, "a join would have returned the two-ruler coin twice"


class TestNumericFields:
    def test_at_least(self, svc, collection):
        assert matched(svc, one("field:weight", "gte", "9")) == {"Victoria penny"}

    def test_less_than(self, svc, collection):
        assert matched(svc, one("field:weight", "lt", "4")) == {"Jiaqing cash"}

    def test_between(self, svc, collection):
        assert matched(svc, one("field:weight", "between", "3", "5")) == {
            "Qianlong cash", "Jiaqing cash",
        }

    def test_the_ends_of_between_can_be_given_either_way_round(self, svc, collection):
        assert matched(svc, one("field:weight", "between", "5", "3")) == {
            "Qianlong cash", "Jiaqing cash",
        }

    def test_a_weight_is_compared_in_its_canonical_unit(self, svc, collection):
        """Weights are kept in grams, so a filter typed in grams must not be read as raw text."""
        assert matched(svc, one("field:weight", "gte", "4.2 g")) == {
            "Qianlong cash", "Victoria penny",
        }

    def test_not_equal_means_no_value_is_that(self, svc, collection):
        found = matched(svc, one("field:weight", "ne", "4.2"))
        assert "Qianlong cash" not in found
        assert "Victoria penny" in found

    def test_a_value_that_is_not_a_number_is_reported_kindly(self, svc, collection):
        with pytest.raises(NumisError, match="not a number"):
            svc.query_specimens(filters=one("field:weight", "gte", "heavy"))


class TestDateFields:
    def test_a_year_inside_a_range_matches(self, svc, collection):
        assert matched(svc, one("field:issued", "in_year", "1750")) == {"Qianlong cash"}

    def test_an_exact_year_matches_itself(self, svc, collection):
        assert matched(svc, one("field:issued", "in_year", "1875")) == {"Victoria penny"}

    def test_before(self, svc, collection):
        assert matched(svc, one("field:issued", "before", "1796")) == {"Qianlong cash"}

    def test_after(self, svc, collection):
        assert matched(svc, one("field:issued", "after", "1820")) == {"Victoria penny"}

    def test_between_years_counts_any_overlap(self, svc, collection):
        found = matched(svc, one("field:issued", "between_years", "1790", "1800"))
        assert found == {"Qianlong cash", "Jiaqing cash"}

    def test_in_a_decade(self, svc, collection):
        assert matched(svc, one("field:issued", "in_decade", "1870")) == {"Victoria penny"}

    def test_in_a_century_means_the_hundred_years_it_names(self, svc, collection):
        """1700 is 1700-1799, the informal reading, so a 1736-1795 reign belongs to it."""
        assert matched(svc, one("field:issued", "in_century", "1700")) == {
            "Qianlong cash", "Jiaqing cash",
        }
        assert matched(svc, one("field:issued", "in_century", "1800")) == {
            "Jiaqing cash", "Victoria penny",
        }

    def test_a_year_bc_is_understood(self, svc, modern):
        svc.create_field("issued", "Issued", "date")
        coin = svc.add_specimen(modern, display_name="Denarius", values={"issued": "c. 350 BC"})
        assert svc.query_specimens(filters=one("field:issued", "before", "1"))[0].id == coin.id

    def test_a_value_that_is_not_a_year_is_reported_kindly(self, svc, collection):
        with pytest.raises(NumisError, match="not a year"):
            svc.query_specimens(filters=one("field:issued", "in_year", "whenever"))


class TestBooleanFields:
    def test_yes(self, svc, collection):
        assert matched(svc, one("field:holed", "is_true")) == {
            "Qianlong cash", "Jiaqing cash",
        }

    def test_no(self, svc, collection):
        assert matched(svc, one("field:holed", "is_false")) == {"Victoria penny"}


class TestTheCoinItself:
    def test_by_id(self, svc, collection):
        assert matched(svc, one("__id__", "is", "10")) == {"Victoria penny"}

    def test_by_a_list_of_ids(self, svc, collection):
        """Requested: hand-pick particular coins so a saved view can hold them."""
        found = matched(svc, one("__id__", "is_any_of", "1", "10", "A7"))
        assert found == {"Qianlong cash", "Victoria penny", "Unidentified"}

    def test_by_name(self, svc, collection):
        assert matched(svc, one("__name__", "contains", "penny")) == {"Victoria penny"}

    def test_by_status(self, svc, collection):
        svc.set_status(collection["victoria"], "sold")
        assert matched(svc, one("__status__", "is", "sold")) == {"Victoria penny"}

    def test_an_unknown_status_is_reported_rather_than_matching_nothing(self, svc, collection):
        with pytest.raises(NumisError, match="unknown status"):
            svc.query_specimens(filters=one("__status__", "is", "flogged"))

    def test_by_subcollection(self, svc, modern, ancients, collection):
        svc.add_specimen(ancients, display_name="Denarius")
        assert matched(svc, one("__subcollection__", "is", "Ancients")) == {"Denarius"}

    def test_by_favourite(self, svc, collection):
        collection["qianlong"].is_favourite = 1
        svc.session.flush()
        assert matched(svc, one("__favourite__", "is_true")) == {"Qianlong cash"}


class TestCatalogueFilters:
    @pytest.fixture
    def catalogued(self, svc, collection):
        krause = svc.create_catalog("KM", "Krause")
        hartill = svc.create_catalog("H", "Hartill")
        svc.add_reference(collection["qianlong"], hartill, "4.11")
        svc.add_reference(collection["jiaqing"], hartill, "6.12")
        svc.add_reference(collection["victoria"], krause, "755")
        return collection

    def test_in_a_catalogue(self, svc, catalogued):
        assert matched(svc, one("catalogues", "in_catalogue", "H")) == {
            "Qianlong cash", "Jiaqing cash",
        }

    def test_not_in_a_catalogue_includes_coins_with_no_numbers_at_all(self, svc, catalogued):
        found = matched(svc, one("catalogues", "not_in_catalogue", "H"))
        assert found == {"Victoria penny", "Unidentified"}

    def test_by_number(self, svc, catalogued):
        assert matched(svc, one("catalogues", "number_is", "755")) == {"Victoria penny"}

    def test_by_part_of_a_number(self, svc, catalogued):
        assert matched(svc, one("catalogues", "number_contains", "4.")) == {"Qianlong cash"}

    def test_with_no_catalogue_number_at_all(self, svc, catalogued):
        assert matched(svc, one("catalogues", "empty")) == {"Unidentified"}


class TestGradeFilters:
    @pytest.fixture
    def graded(self, svc, collection, sheldon, modifiers):
        svc.add_grade(
            collection["qianlong"], sheldon, "MS63", base_value=63.0,
            source="tpg", assigned_by="PCGS",
        )
        svc.add_grade(
            collection["jiaqing"], sheldon, "VF30", base_value=30.0,
            modifiers=[("DETAILS", "Harshly Cleaned")], assigned_by="NGC",
        )
        svc.add_grade(
            collection["victoria"], sheldon, "AU58", base_value=58.0, assigned_by="Bob Reis",
        )
        return collection

    def test_at_least_compares_the_calculated_value(self, svc, graded):
        """The point of the calculated value: grades from any standard compare as numbers."""
        assert matched(svc, one("grades", "at_least", "58")) == {
            "Qianlong cash", "Victoria penny",
        }

    def test_at_most(self, svc, graded):
        assert matched(svc, one("grades", "at_most", "40")) == {"Jiaqing cash"}

    def test_between(self, svc, graded):
        assert matched(svc, one("grades", "between", "50", "60")) == {"Victoria penny"}

    def test_a_modifier_lowers_the_value_it_is_compared_at(self, svc, graded):
        """VF30 Details calculates at 29.6, so it is below a plain VF30."""
        assert matched(svc, one("grades", "at_most", "29.9")) == {"Jiaqing cash"}

    def test_by_who_graded_it(self, svc, graded):
        assert matched(svc, one("grades", "graded_by", "PCGS")) == {"Qianlong cash"}

    def test_by_where_the_grade_came_from(self, svc, graded):
        assert matched(svc, one("grades", "graded_by", "tpg")) == {"Qianlong cash"}

    def test_problem_grades_can_be_singled_out(self, svc, graded):
        assert matched(svc, one("grades", "is_problem")) == {"Jiaqing cash"}

    def test_by_a_particular_modifier(self, svc, graded):
        assert matched(svc, one("grades", "has_modifier", "DETAILS")) == {"Jiaqing cash"}

    def test_ungraded_coins(self, svc, graded):
        assert matched(svc, one("grades", "empty")) == {"Unidentified"}


class TestCertificationFilters:
    @pytest.fixture
    def certified(self, svc, collection):
        ngc = svc.create_grading_company("NGC", "NGC")
        pcgs = svc.create_grading_company("PCGS", "PCGS")
        svc.add_certification(collection["qianlong"], ngc, cert_number="111")
        svc.add_certification(collection["victoria"], pcgs, cert_number="222")
        return collection

    def test_certified_by(self, svc, certified):
        assert matched(svc, one("certifications", "certified_by", "NGC")) == {"Qianlong cash"}

    def test_not_certified_by_includes_the_uncertified(self, svc, certified):
        found = matched(svc, one("certifications", "not_certified_by", "NGC"))
        assert found == {"Jiaqing cash", "Victoria penny", "Unidentified"}

    def test_by_certificate_number(self, svc, certified):
        assert matched(svc, one("certifications", "number_is", "222")) == {"Victoria penny"}

    def test_a_cracked_out_slab_no_longer_counts_as_certified(self, svc, certified):
        certification = svc.current_certifications(certified["qianlong"])[0]
        certification.status = "cracked_out"
        svc.session.flush()
        assert matched(svc, one("certifications", "certified_by", "NGC")) == set()


class TestLinkFilters:
    def test_by_kind(self, svc, collection):
        svc.add_link(collection["qianlong"], "https://zeno.ru/x", kind="zeno")
        assert matched(svc, one("links", "of_kind", "zeno")) == {"Qianlong cash"}

    def test_coins_with_no_links(self, svc, collection):
        svc.add_link(collection["qianlong"], "https://zeno.ru/x", kind="zeno")
        assert "Qianlong cash" not in matched(svc, one("links", "empty"))


class TestCombiningCriteria:
    def test_all_must_match(self, svc, collection):
        group = FilterGroup.of(
            Criterion("field:metal", "is", ("bronze",)),
            Criterion("field:weight", "lt", ("4",)),
        )
        assert matched(svc, group) == {"Jiaqing cash"}

    def test_any_is_enough(self, svc, collection):
        group = FilterGroup.of(
            Criterion("field:ruler", "is", ("Qianlong",)),
            Criterion("field:ruler", "is", ("Victoria",)),
            match="any",
        )
        assert matched(svc, group) == {"Qianlong cash", "Victoria penny"}

    def test_a_nested_group_asks_the_question_a_collector_has(self, svc, collection):
        """Bronze, and either Qianlong or Jiaqing — which no flat list of conditions expresses."""
        rulers = FilterGroup.of(
            Criterion("field:ruler", "is", ("Qianlong",)),
            Criterion("field:ruler", "is", ("Victoria",)),
            match="any",
        )
        group = FilterGroup(
            criteria=(Criterion("field:metal", "is", ("bronze",)),), groups=(rulers,)
        )
        assert matched(svc, group) == {"Qianlong cash"}

    def test_a_group_can_be_negated(self, svc, collection):
        group = FilterGroup(
            criteria=(Criterion("field:metal", "is", ("bronze",)),), negate=True
        )
        assert matched(svc, group) == {"Victoria penny", "Unidentified"}

    def test_no_filter_returns_everything(self, svc, collection):
        assert len(svc.query_specimens()) == 4
        assert len(svc.query_specimens(filters=FilterGroup())) == 4


class TestCounting:
    def test_matches_can_be_counted_without_loading_them(self, svc, collection):
        assert svc.count_specimens(filters=one("field:metal", "is", "bronze")) == 2

    def test_counting_with_no_filter_counts_everything(self, svc, collection):
        assert svc.count_specimens() == 4

    def test_a_coin_with_several_values_is_counted_once(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text", is_multi=True)
        coin = svc.add_specimen(modern)
        svc.set_value(coin, "ruler", "Victoria", seq=0)
        svc.set_value(coin, "ruler", "Albert", seq=1)
        assert svc.count_specimens(filters=one("field:ruler", "not_empty")) == 1


class TestSortingByOneKey:
    def test_by_a_number(self, svc, collection):
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("field:weight")])]
        assert names[:3] == ["Jiaqing cash", "Qianlong cash", "Victoria penny"]

    def test_missing_values_go_last_ascending(self, svc, collection):
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("field:weight")])]
        assert names[-1] == "Unidentified"

    def test_missing_values_go_last_descending_too(self, svc, collection):
        """A blank is absent, not smaller than everything."""
        names = [
            s.display_name
            for s in svc.query_specimens(sort=[SortKey("field:weight", descending=True)])
        ]
        assert names[0] == "Victoria penny"
        assert names[-1] == "Unidentified"

    def test_by_a_date_using_its_sort_value(self, svc, collection):
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("field:issued")])]
        assert names[:3] == ["Qianlong cash", "Jiaqing cash", "Victoria penny"]

    def test_by_name_ignoring_capitals(self, svc, collection):
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("__name__")])]
        assert names == sorted(names, key=str.lower)

    def test_by_id_numerically(self, svc, collection):
        """2 before 10, and a non-numeric identifier after both."""
        codes = [s.inventory_code for s in svc.query_specimens(sort=[SortKey("__id__")])]
        assert codes == ["1", "2", "10", "A7"]

    def test_by_text_ignoring_capitals(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text")
        for name in ("bravo", "Alpha", "charlie"):
            svc.add_specimen(modern, display_name=name, values={"ruler": name})
        rulers = [
            svc.display(s, "ruler") for s in svc.query_specimens(sort=[SortKey("field:ruler")])
        ]
        assert rulers == ["Alpha", "bravo", "charlie"]

    def test_a_field_that_cannot_be_sorted_says_so(self, svc, collection):
        with pytest.raises(NumisError, match="cannot be sorted"):
            svc.query_specimens(sort=[SortKey("field:notes")])

    def test_sorting_by_a_column_that_has_gone_is_ignored(self, svc, collection):
        assert len(svc.query_specimens(sort=[SortKey("field:nonexistent")])) == 4


class TestSortingBySeveralKeys:
    @pytest.fixture
    def grouped(self, svc, modern):
        svc.create_field("country", "Country", "text")
        svc.create_field("issued", "Issued", "date")
        for country, year, name in (
            ("China", "1750", "China 1750"),
            ("China", "1700", "China 1700"),
            ("Britain", "1875", "Britain 1875"),
            ("Britain", "1800", "Britain 1800"),
        ):
            svc.add_specimen(
                modern, display_name=name, values={"country": country, "issued": year}
            )

    def test_the_second_key_orders_within_the_first(self, svc, grouped):
        names = [
            s.display_name
            for s in svc.query_specimens(
                sort=[SortKey("field:country"), SortKey("field:issued")]
            )
        ]
        assert names == ["Britain 1800", "Britain 1875", "China 1700", "China 1750"]

    def test_each_key_keeps_its_own_direction(self, svc, grouped):
        names = [
            s.display_name
            for s in svc.query_specimens(
                sort=[SortKey("field:country"), SortKey("field:issued", descending=True)]
            )
        ]
        assert names == ["Britain 1875", "Britain 1800", "China 1750", "China 1700"]

    def test_three_keys_are_no_different_from_two(self, svc, grouped):
        found = svc.query_specimens(
            sort=[SortKey("field:country"), SortKey("field:issued"), SortKey("__name__")]
        )
        assert len(found) == 4


class TestSortingBySpecialColumns:
    def test_grades_sort_by_their_calculated_value(self, svc, modern, sheldon, modifiers):
        """Requested: the calculated value is what sorting compares."""
        low = svc.add_specimen(modern, display_name="VF")
        high = svc.add_specimen(modern, display_name="MS")
        problem = svc.add_specimen(modern, display_name="MS Details")
        svc.add_grade(low, sheldon, "VF30", base_value=30.0)
        svc.add_grade(high, sheldon, "MS63", base_value=63.0)
        svc.add_grade(problem, sheldon, "MS63", base_value=63.0,
                      modifiers=[("DETAILS", "Cleaned")])

        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("grades")])]
        assert names == ["VF", "MS Details", "MS"]

    def test_a_plus_sorts_above_the_grade_it_qualifies(self, svc, modern, sheldon, modifiers):
        plain = svc.add_specimen(modern, display_name="MS63")
        plus = svc.add_specimen(modern, display_name="MS63+")
        svc.add_grade(plain, sheldon, "MS63", base_value=63.0)
        svc.add_grade(plus, sheldon, "MS63", base_value=63.0, modifiers=[("PLUS", None)])
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("grades")])]
        assert names == ["MS63", "MS63+"]

    def test_ungraded_coins_sort_last(self, svc, modern, sheldon):
        graded = svc.add_specimen(modern, display_name="Graded")
        svc.add_specimen(modern, display_name="Ungraded")
        svc.add_grade(graded, sheldon, "VF30", base_value=30.0)
        for descending in (False, True):
            names = [
                s.display_name
                for s in svc.query_specimens(sort=[SortKey("grades", descending=descending)])
            ]
            assert names[-1] == "Ungraded"

    def test_catalogue_numbers_sort_in_catalogue_order(self, svc, modern):
        hartill = svc.create_catalog("H", "Hartill")
        for number, name in (("4.11", "H 4.11"), ("4.2", "H 4.2"), ("22.1", "H 22.1")):
            coin = svc.add_specimen(modern, display_name=name)
            svc.add_reference(coin, hartill, number)
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("catalogues")])]
        assert names == ["H 4.2", "H 4.11", "H 22.1"]

    def test_a_catalogue_column_can_sort_by_its_own_catalogue(self, svc, modern):
        """A column showing only Hartill must sort by Hartill, not by whatever ranks first."""
        hartill = svc.create_catalog("H", "Hartill")
        krause = svc.create_catalog("KM", "Krause")
        first = svc.add_specimen(modern, display_name="Second in Hartill")
        second = svc.add_specimen(modern, display_name="First in Hartill")
        svc.add_reference(first, krause, "1", rank=1)
        svc.add_reference(first, hartill, "9.9", rank=2)
        svc.add_reference(second, hartill, "1.1", rank=1)

        names = [
            s.display_name
            for s in svc.query_specimens(
                sort=[SortKey("catalogues")], catalogues={"catalogues": "H"}
            )
        ]
        assert names == ["First in Hartill", "Second in Hartill"]

    def test_links_sort_by_how_many_there_are(self, svc, modern):
        none = svc.add_specimen(modern, display_name="None")
        two = svc.add_specimen(modern, display_name="Two")
        svc.add_link(two, "https://a.invalid")
        svc.add_link(two, "https://b.invalid")
        names = [
            s.display_name
            for s in svc.query_specimens(sort=[SortKey("links", descending=True)])
        ]
        assert names == ["Two", "None"]
        assert none.display_name == "None"

    def test_certifications_sort_by_company(self, svc, modern):
        ngc = svc.create_grading_company("NGC", "NGC")
        anacs = svc.create_grading_company("ANACS", "ANACS")
        first = svc.add_specimen(modern, display_name="NGC coin")
        second = svc.add_specimen(modern, display_name="ANACS coin")
        svc.add_certification(first, ngc, cert_number="1")
        svc.add_certification(second, anacs, cert_number="2")
        names = [s.display_name for s in svc.query_specimens(sort=[SortKey("certifications")])]
        assert names == ["ANACS coin", "NGC coin"]


class TestSearchingAndFilteringTogether:
    def test_a_search_term_narrows_the_result(self, svc, collection):
        found = svc.query_specimens(term="Victoria")
        assert [s.display_name for s in found] == ["Victoria penny"]

    def test_a_search_can_be_combined_with_a_filter(self, svc, collection):
        group = one("field:metal", "is", "copper")
        assert matched(svc, group, term="Victoria") == {"Victoria penny"}

    def test_a_filter_that_excludes_the_match_returns_nothing(self, svc, collection):
        assert matched(svc, one("field:metal", "is", "bronze"), term="Victoria") == set()

    def test_a_search_still_honours_the_sort(self, svc, collection):
        """Searching used to replace the sort order silently."""
        found = svc.query_specimens(term="cash", sort=[SortKey("field:weight")])
        assert [s.display_name for s in found] == ["Jiaqing cash", "Qianlong cash"]

    def test_a_term_matching_nothing_returns_nothing(self, svc, collection):
        assert svc.query_specimens(term="Napoleon") == []

    def test_an_empty_term_is_not_a_filter(self, svc, collection):
        assert len(svc.query_specimens(term="   ")) == 4


class TestTheIndexKeepsItself:
    """Search used to go stale: the interface never rebuilt the index after an edit."""

    def test_a_new_coin_is_findable_without_asking(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text")
        svc.add_specimen(modern, display_name="Maria Theresia Thaler")
        assert [s.display_name for s in svc.query_specimens(term="Theresia")] == [
            "Maria Theresia Thaler"
        ]

    def test_an_edited_value_is_findable_immediately(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text")
        coin = svc.add_specimen(modern, display_name="A coin")
        svc.set_value(coin, "ruler", "Franz Joseph")
        assert svc.query_specimens(term="Joseph")

    def test_an_old_value_stops_being_found(self, svc, modern):
        svc.create_field("ruler", "Ruler", "text")
        coin = svc.add_specimen(modern, values={"ruler": "Maria Theresia"})
        assert svc.query_specimens(term="Theresia")
        svc.set_value(coin, "ruler", "Franz Joseph")
        assert svc.query_specimens(term="Theresia") == []

    def test_a_renamed_coin_is_found_by_its_new_name(self, svc, modern):
        coin = svc.add_specimen(modern, display_name="Old name")
        svc.set_display_name(coin, "New name")
        assert svc.query_specimens(term="New")
        assert svc.query_specimens(term="Old") == []

    def test_a_new_catalogue_number_is_findable(self, svc, modern):
        krause = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern, display_name="Thaler")
        svc.add_reference(coin, krause, "2073")
        assert [s.id for s in svc.query_specimens(term="2073")] == [coin.id]

    def test_a_new_identifier_is_findable(self, svc, modern):
        coin = svc.add_specimen(modern, display_name="Thaler")
        svc.set_inventory_code(coin, "CN-0042")
        assert [s.id for s in svc.query_specimens(term="CN-0042")] == [coin.id]


class TestScopeAndStatus:
    def test_a_filter_stays_inside_the_chosen_subcollection(
        self, svc, modern, ancients, collection
    ):
        svc.add_specimen(ancients, display_name="Bronze denarius")
        found = svc.query_specimens(ancients, filters=one("__name__", "contains", "bronze"))
        assert [s.display_name for s in found] == ["Bronze denarius"]

    def test_trashed_coins_stay_out_unless_asked_for(self, svc, collection):
        svc.soft_delete(collection["victoria"])
        assert "Victoria penny" not in matched(svc, FilterGroup())
        assert "Victoria penny" in matched(svc, FilterGroup(), include_deleted=True)

    def test_disposed_coins_can_be_excluded_while_filtering(self, svc, collection):
        svc.set_status(collection["victoria"], "sold")
        found = matched(svc, one("field:metal", "not_empty"), include_disposed=False)
        assert "Victoria penny" not in found

    def test_a_sort_still_honours_the_trash_setting(self, svc, collection):
        svc.soft_delete(collection["victoria"])
        found = svc.query_specimens(sort=[SortKey("field:weight")])
        assert "Victoria penny" not in [s.display_name for s in found]


class TestSavedViews:
    def test_a_view_remembers_its_filter_and_sort(self, svc, modern, collection):
        group = one("field:metal", "is", "bronze")
        keys = [SortKey("field:weight", descending=True)]
        svc.save_view("Bronze by weight", subcollection=modern, filters=group, sort=keys)

        view = svc.view_by_name("Bronze by weight")
        assert svc.view_filter(view) == group
        assert svc.view_sort(view) == tuple(keys)

    def test_a_saved_view_returns_the_coins_it_describes(self, svc, modern, collection):
        svc.save_view(
            "Bronze",
            subcollection=modern,
            filters=one("field:metal", "is", "bronze"),
            sort=[SortKey("field:weight")],
        )
        view = svc.view_by_name("Bronze")
        found = svc.query_specimens(
            modern, filters=svc.view_filter(view), sort=svc.view_sort(view)
        )
        assert [s.display_name for s in found] == ["Jiaqing cash", "Qianlong cash"]

    def test_saving_the_same_name_replaces_it(self, svc, modern):
        svc.save_view("Mine", filters=one("__status__", "is", "owned"))
        svc.save_view("Mine", filters=one("__status__", "is", "sold"))
        assert len(svc.views()) == 1
        assert svc.view_filter(svc.view_by_name("Mine")).criteria[0].values == ("sold",)

    def test_a_name_is_required(self, svc):
        with pytest.raises(NumisError, match="needs a name"):
            svc.save_view("   ")

    def test_views_for_a_subcollection_include_the_ones_that_apply_anywhere(
        self, svc, modern, ancients
    ):
        svc.save_view("Everything owned", filters=one("__status__", "is", "owned"))
        svc.save_view("Modern bronze", subcollection=modern, filters=one("field:metal", "is", "x"))
        svc.save_view("Ancient silver", subcollection=ancients, filters=one("__id__", "not_empty"))

        names = {view.name for view in svc.views(modern)}
        assert names == {"Everything owned", "Modern bronze"}

    def test_a_view_can_hold_particular_coins(self, svc, modern, collection):
        """The reason 'is any of' exists: a hand-picked set that survives being reopened."""
        chosen = [collection["qianlong"].inventory_code, collection["victoria"].inventory_code]
        svc.save_view("Favourites", filters=one("__id__", "is_any_of", *chosen))

        view = svc.view_by_name("Favourites")
        found = svc.query_specimens(filters=svc.view_filter(view))
        assert {s.display_name for s in found} == {"Qianlong cash", "Victoria penny"}

    def test_a_view_can_be_deleted(self, svc):
        svc.save_view("Temporary", filters=one("__status__", "is", "owned"))
        svc.delete_view(svc.view_by_name("Temporary"))
        assert svc.views() == []

    def test_looking_up_a_view_ignores_capitals(self, svc):
        svc.save_view("Chinese cash", filters=one("__status__", "is", "owned"))
        assert svc.view_by_name("chinese CASH") is not None
