"""Building, saving and describing a filter, without touching a database."""

from __future__ import annotations

import pytest

from numis.filters import (
    NO_FILTER,
    Criterion,
    FilterError,
    FilterGroup,
    SortKey,
    add_sort_key,
    describe_sort,
    expected_values,
    operators_for,
    sort_from_json,
    sort_to_json,
)


class TestCriteria:
    def test_a_field_criterion_knows_its_field(self):
        assert Criterion("field:ruler", "contains", ("Vic",)).field_key == "ruler"

    def test_an_identity_criterion_has_no_field(self):
        assert Criterion("__status__", "is", ("owned",)).field_key is None

    def test_something_must_be_tested(self):
        with pytest.raises(FilterError):
            Criterion("", "is", ("x",))

    def test_an_operator_is_required(self):
        with pytest.raises(FilterError):
            Criterion("__name__", "", ())


class TestOperatorsComeFromTheRegistry:
    def test_a_text_field_offers_text_operators(self):
        operators = operators_for("field:ruler", "text")
        assert "contains" in operators
        assert "starts_with" in operators

    def test_a_date_field_offers_date_operators(self):
        operators = operators_for("field:issued", "date")
        assert "in_decade" in operators
        assert "contains" not in operators

    def test_a_number_field_offers_comparisons(self):
        operators = operators_for("field:weight", "weight")
        assert {"lt", "gte", "between"} <= set(operators)

    def test_a_long_text_field_is_deliberately_limited(self):
        operators = operators_for("field:notes", "long_text")
        assert "contains" in operators
        assert "is" not in operators

    def test_the_id_column_can_be_asked_for_a_list(self):
        """Requested: pick out particular coins by ID so a view can hold them."""
        assert "is_any_of" in operators_for("__id__")

    def test_each_system_has_its_own_operators(self):
        assert "in_catalogue" in operators_for("catalogues")
        assert "at_least" in operators_for("grades")
        assert "certified_by" in operators_for("certifications")
        assert "of_kind" in operators_for("links")

    def test_a_field_target_needs_its_type(self):
        with pytest.raises(FilterError):
            operators_for("field:ruler")

    def test_an_unknown_target_is_refused(self):
        with pytest.raises(FilterError):
            operators_for("something_else")


class TestHowManyValues:
    def test_presence_operators_take_none(self):
        assert expected_values("empty") == 0
        assert expected_values("not_empty") == 0

    def test_between_takes_two(self):
        assert expected_values("between") == 2
        assert expected_values("between_years") == 2

    def test_a_list_takes_one_or_more(self):
        assert expected_values("is_any_of") == -1

    def test_everything_else_takes_one(self):
        assert expected_values("contains") == 1


class TestValidation:
    def test_a_missing_value_is_reported_with_the_column_s_name(self):
        criterion = Criterion("field:ruler", "contains", ())
        with pytest.raises(FilterError, match="Ruler"):
            criterion.validate("Ruler")

    def test_between_needs_both_ends(self):
        with pytest.raises(FilterError, match="2 value"):
            Criterion("field:weight", "between", ("5",)).validate("Weight")

    def test_a_presence_operator_must_not_be_given_a_value(self):
        with pytest.raises(FilterError):
            Criterion("field:ruler", "empty", ("something",)).validate()

    def test_a_list_needs_at_least_one_entry(self):
        with pytest.raises(FilterError):
            Criterion("__id__", "is_any_of", ()).validate()

    def test_blank_values_do_not_count(self):
        with pytest.raises(FilterError):
            Criterion("field:ruler", "contains", ("   ",)).validate()

    def test_a_valid_criterion_passes_quietly(self):
        Criterion("field:ruler", "contains", ("Victoria",)).validate()

    def test_a_group_checks_everything_inside_it(self):
        group = FilterGroup.of(
            Criterion("field:ruler", "contains", ("Victoria",)),
            Criterion("field:weight", "between", ("5",)),
        )
        with pytest.raises(FilterError):
            group.validate()


class TestEmptiness:
    def test_a_group_with_nothing_in_it_is_no_filter(self):
        assert not FilterGroup()
        assert FilterGroup().is_empty()
        assert NO_FILTER.is_empty()

    def test_a_group_holding_only_an_empty_group_is_still_no_filter(self):
        assert FilterGroup(groups=(FilterGroup(),)).is_empty()

    def test_one_criterion_makes_it_a_filter(self):
        assert FilterGroup.of(Criterion("__status__", "is", ("owned",)))

    def test_criteria_are_counted_at_any_depth(self):
        inner = FilterGroup.of(
            Criterion("__status__", "is", ("owned",)),
            Criterion("__name__", "contains", ("a",)),
        )
        outer = FilterGroup(criteria=(Criterion("__id__", "not_empty"),), groups=(inner,))
        assert outer.count() == 3


class TestNesting:
    def test_groups_can_hold_groups(self):
        inner = FilterGroup.of(
            Criterion("field:ruler", "is", ("Qianlong",)),
            Criterion("field:ruler", "is", ("Jiaqing",)),
            match="any",
        )
        outer = FilterGroup(
            criteria=(Criterion("field:metal", "is", ("bronze",)),), groups=(inner,)
        )
        assert outer.match == "all"
        assert outer.groups[0].match == "any"

    def test_a_group_matches_all_or_any_only(self):
        with pytest.raises(FilterError):
            FilterGroup(match="some")


class TestStorage:
    def _filter(self) -> FilterGroup:
        return FilterGroup(
            match="all",
            criteria=(Criterion("field:metal", "is", ("bronze",)),),
            groups=(
                FilterGroup(
                    match="any",
                    criteria=(
                        Criterion("field:ruler", "is", ("Qianlong",)),
                        Criterion("field:ruler", "is", ("Jiaqing",)),
                    ),
                ),
            ),
        )

    def test_a_filter_survives_a_round_trip(self):
        original = self._filter()
        assert FilterGroup.from_json(original.to_json()) == original

    def test_a_negated_group_survives(self):
        original = FilterGroup(
            criteria=(Criterion("__status__", "is", ("sold",)),), negate=True
        )
        assert FilterGroup.from_json(original.to_json()).negate is True

    def test_nothing_is_stored_for_an_empty_filter(self):
        assert FilterGroup().to_dict() == {"match": "all"}

    def test_unreadable_json_opens_as_no_filter(self):
        assert FilterGroup.from_json("{oh dear").is_empty()
        assert FilterGroup.from_json("[]").is_empty()
        assert FilterGroup.from_json(None).is_empty()

    def test_an_unknown_match_becomes_all(self):
        assert FilterGroup.from_dict({"match": "sideways"}).match == "all"

    def test_a_criterion_that_cannot_be_read_is_skipped(self):
        """A view saved by a later version should still open with the parts that make sense."""
        loaded = FilterGroup.from_dict(
            {
                "criteria": [
                    {"target": "field:ruler", "operator": "is", "values": ["Victoria"]},
                    {"operator": "is", "values": ["x"]},  # no target
                    "not even an object",
                ]
            }
        )
        assert loaded.count() == 1
        assert loaded.criteria[0].field_key == "ruler"

    def test_a_single_value_stored_as_a_string_is_accepted(self):
        criterion = Criterion.from_dict({"target": "__name__", "operator": "is", "values": "x"})
        assert criterion.values == ("x",)


class TestDescribing:
    def test_no_filter_says_so(self):
        assert FilterGroup().describe() == "no filter"

    def test_criteria_read_as_a_sentence(self):
        group = FilterGroup.of(
            Criterion("field:ruler", "contains", ("Victoria",)),
            Criterion("__status__", "is", ("owned",)),
        )
        assert group.describe() == "ruler contains Victoria and Status is owned"

    def test_any_reads_as_or(self):
        group = FilterGroup.of(
            Criterion("field:ruler", "is", ("Qianlong",)),
            Criterion("field:ruler", "is", ("Jiaqing",)),
            match="any",
        )
        assert " or " in group.describe()

    def test_labels_are_used_when_given(self):
        group = FilterGroup.of(Criterion("field:ruler", "contains", ("Victoria",)))
        assert group.describe({"field:ruler": "Ruler"}) == "Ruler contains Victoria"

    def test_a_presence_operator_needs_no_value(self):
        assert Criterion("field:notes", "empty").describe("Notes") == "Notes is empty"

    def test_between_names_both_ends(self):
        criterion = Criterion("field:weight", "between", ("5", "10"))
        assert criterion.describe("Weight") == "Weight is between 5 and 10"

    def test_a_list_names_everything_in_it(self):
        criterion = Criterion("__id__", "is_any_of", ("3", "7", "11"))
        assert criterion.describe() == "ID is any of 3, 7, 11"

    def test_a_nested_group_is_bracketed(self):
        inner = FilterGroup.of(
            Criterion("field:ruler", "is", ("Qianlong",)),
            Criterion("field:ruler", "is", ("Jiaqing",)),
            match="any",
        )
        outer = FilterGroup(
            criteria=(Criterion("field:metal", "is", ("bronze",)),), groups=(inner,)
        )
        described = outer.describe()
        assert described.startswith("metal is bronze and (")
        assert " or " in described

    def test_a_negated_group_says_not(self):
        group = FilterGroup(criteria=(Criterion("__status__", "is", ("sold",)),), negate=True)
        assert group.describe().startswith("not (")


class TestEditingAGroup:
    def test_a_criterion_can_be_added(self):
        group = FilterGroup().with_criterion(Criterion("__name__", "contains", ("a",)))
        assert group.count() == 1

    def test_a_criterion_can_be_removed_by_position(self):
        group = FilterGroup.of(
            Criterion("__name__", "contains", ("a",)),
            Criterion("__status__", "is", ("owned",)),
        )
        assert group.without(0).criteria[0].target == "__status__"

    def test_the_original_is_left_alone(self):
        group = FilterGroup.of(Criterion("__name__", "contains", ("a",)))
        group.with_criterion(Criterion("__status__", "is", ("owned",)))
        assert group.count() == 1


class TestSortKeys:
    def test_a_key_survives_a_round_trip(self):
        keys = (SortKey("field:weight", descending=True), SortKey("__name__"))
        assert sort_from_json(sort_to_json(keys)) == keys

    def test_unreadable_json_is_no_sort(self):
        assert sort_from_json("{oh dear") == ()
        assert sort_from_json(None) == ()
        assert sort_from_json('{"target": "x"}') == ()

    def test_entries_without_a_target_are_skipped(self):
        assert sort_from_json('[{"descending": true}]') == ()

    def test_an_ordinary_click_sorts_by_that_column_alone(self):
        existing = (SortKey("__name__"),)
        assert add_sort_key(existing, "field:weight", descending=False, additional=False) == (
            SortKey("field:weight"),
        )

    def test_adding_a_key_keeps_the_ones_already_chosen(self):
        existing = (SortKey("field:country"),)
        result = add_sort_key(existing, "field:date", descending=True, additional=True)
        assert result == (SortKey("field:country"), SortKey("field:date", descending=True))

    def test_re_adding_a_column_moves_it_rather_than_duplicating_it(self):
        existing = (SortKey("field:country"), SortKey("field:date"))
        result = add_sort_key(existing, "field:country", descending=True, additional=True)
        assert result == (SortKey("field:date"), SortKey("field:country", descending=True))

    def test_a_sort_reads_as_a_sentence(self):
        keys = (SortKey("field:country"), SortKey("field:date", descending=True))
        described = describe_sort(keys, {"field:country": "Country", "field:date": "Date"})
        assert described == "Country ascending, then Date descending"

    def test_no_sort_says_what_the_order_actually_is(self):
        assert describe_sort(()) == "the order they were added"

    def test_the_identity_columns_read_by_their_names(self):
        assert SortKey("__id__").describe() == "ID ascending"
