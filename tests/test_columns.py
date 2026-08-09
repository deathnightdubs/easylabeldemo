"""Column display settings: reading, writing and narrowing.

These are the rules behind "show all / only Hartill / only the one I ranked second", tested
without a database or a GUI because that is all they need.
"""

from __future__ import annotations

import pytest

from numis.columns import DEFAULT_DISPLAY, MAX_RANK, ColumnDisplay, pick


class TestDefaults:
    def test_a_fresh_column_shows_everything_it_has(self):
        display = ColumnDisplay()
        assert display.mode == "all"
        assert display.only is None
        assert display.rank == 1

    def test_modifiers_are_shown_but_not_spelled_out(self):
        """MS63 CAC by default; MS63 CAC Gold only when asked."""
        display = ColumnDisplay()
        assert display.show_modifiers is True
        assert display.modifier_details is False

    def test_the_extras_stay_out_of_the_way(self):
        display = ColumnDisplay()
        assert (display.show_scale, display.show_source, display.show_assigned_by) == (
            False,
            False,
            False,
        )

    def test_a_catalogue_number_carries_its_catalogue(self):
        assert ColumnDisplay().show_catalogue is True

    def test_links_are_counted_rather_than_listed(self):
        assert ColumnDisplay().show_labels is False

    def test_an_unknown_mode_is_refused_when_constructed_directly(self):
        with pytest.raises(ValueError, match="unknown column mode"):
            ColumnDisplay(mode="whatever")


class TestGradeDisplayHandover:
    """The grade flags must arrive in :mod:`numis.grading` unchanged."""

    def test_every_flag_is_passed_through(self):
        display = ColumnDisplay(
            show_modifiers=False,
            modifier_details=True,
            show_scale=True,
            show_source=True,
            show_assigned_by=True,
        )
        handed = display.grade_display
        assert handed.modifiers is False
        assert handed.modifier_details is True
        assert handed.scale is True
        assert handed.source is True
        assert handed.assigned_by is True

    def test_the_default_matches_grading_s_own_default(self):
        from numis.grading import GradeDisplay

        assert ColumnDisplay().grade_display == GradeDisplay()


class TestStorage:
    def test_defaults_are_stored_as_nothing_at_all(self):
        """Keeps saved settings readable, and lets later defaults reach old columns."""
        assert ColumnDisplay().to_config() == {}
        assert ColumnDisplay().to_json() == "{}"

    def test_only_what_the_user_changed_is_written(self):
        display = ColumnDisplay(mode="only", only="H", show_catalogue=False)
        assert display.to_config() == {"mode": "only", "only": "H", "show_catalogue": False}

    def test_settings_survive_a_round_trip(self):
        display = ColumnDisplay(
            mode="rank",
            rank=3,
            separator=" | ",
            show_modifiers=False,
            modifier_details=True,
            show_scale=True,
            show_source=True,
            show_assigned_by=True,
            show_catalogue=False,
            show_labels=True,
        )
        assert ColumnDisplay.from_json(display.to_json()) == display

    def test_an_empty_column_reads_as_the_defaults(self):
        assert ColumnDisplay.from_json("{}") == DEFAULT_DISPLAY
        assert ColumnDisplay.from_json(None) == DEFAULT_DISPLAY
        assert ColumnDisplay.from_config({}) == DEFAULT_DISPLAY


class TestForgivingReads:
    """A column whose settings cannot be understood must still draw."""

    def test_unreadable_json_falls_back_to_the_defaults(self):
        assert ColumnDisplay.from_json("{not json at all") == DEFAULT_DISPLAY

    def test_a_json_value_that_is_not_an_object_is_ignored(self):
        assert ColumnDisplay.from_json("[1, 2, 3]") == DEFAULT_DISPLAY
        assert ColumnDisplay.from_json("7") == DEFAULT_DISPLAY

    def test_unknown_keys_are_dropped_rather_than_crashing(self):
        display = ColumnDisplay.from_config({"mode": "rank", "invented_later": True})
        assert display.mode == "rank"

    def test_an_unknown_mode_becomes_show_all(self):
        assert ColumnDisplay.from_config({"mode": "sideways"}).mode == "all"

    def test_rank_is_clamped_to_something_usable(self):
        assert ColumnDisplay.from_config({"mode": "rank", "rank": 0}).rank == 1
        assert ColumnDisplay.from_config({"mode": "rank", "rank": -5}).rank == 1
        assert ColumnDisplay.from_config({"mode": "rank", "rank": 99}).rank == MAX_RANK

    def test_a_nonsense_rank_becomes_the_first(self):
        assert ColumnDisplay.from_config({"rank": "third"}).rank == 1
        assert ColumnDisplay.from_config({"rank": None}).rank == 1

    def test_an_empty_filter_is_no_filter(self):
        assert ColumnDisplay.from_config({"mode": "only", "only": ""}).only is None

    def test_flags_are_coerced_to_true_or_false(self):
        display = ColumnDisplay.from_config({"show_modifiers": 0, "show_scale": 1})
        assert display.show_modifiers is False
        assert display.show_scale is True

    def test_a_nonsense_separator_falls_back(self):
        assert ColumnDisplay.from_config({"separator": 5}).separator == " · "


class TestNarrowing:
    def test_show_all_keeps_the_order_it_was_given(self):
        assert pick(["a", "b", "c"], ColumnDisplay()) == ["a", "b", "c"]

    def test_only_is_applied_by_the_caller_not_here(self):
        """``pick`` knows about precedence; matching a catalogue needs the database."""
        assert pick(["a", "b"], ColumnDisplay(mode="only", only="H")) == ["a", "b"]

    def test_rank_takes_the_entry_at_that_position(self):
        entries = ["first", "second", "third"]
        assert pick(entries, ColumnDisplay(mode="rank", rank=1)) == ["first"]
        assert pick(entries, ColumnDisplay(mode="rank", rank=2)) == ["second"]
        assert pick(entries, ColumnDisplay(mode="rank", rank=3)) == ["third"]

    def test_asking_for_a_rank_the_coin_has_not_got_gives_nothing(self):
        """A blank cell, not the wrong entry: this coin simply has no third catalogue number."""
        assert pick(["only one"], ColumnDisplay(mode="rank", rank=2)) == []
        assert pick([], ColumnDisplay(mode="rank", rank=1)) == []

    def test_position_is_counted_rather_than_the_stored_number(self):
        """Ranks are not uniquely enforced, so the third one down is what the user meant."""
        entries = ["a", "b"]  # already in the user's order, whatever integers they carry
        assert pick(entries, ColumnDisplay(mode="rank", rank=2)) == ["b"]


class TestDescribingItself:
    def test_it_says_when_it_shows_everything(self):
        assert ColumnDisplay().describe("catalogues") == "All entries"

    def test_it_names_the_thing_it_is_filtered_to(self):
        display = ColumnDisplay(mode="only", only="H")
        assert display.describe("catalogues") == "Only catalogue H"
        assert display.describe("certifications") == "Only company H"
        assert display.describe("grades") == "Only source or grader H"
        assert display.describe("links") == "Only kind H"

    def test_it_says_which_rank_it_shows(self):
        assert ColumnDisplay(mode="rank", rank=2).describe("grades") == "The entry ranked 2"

    def test_a_filter_with_nothing_chosen_reads_as_show_all(self):
        assert ColumnDisplay(mode="only").describe("grades") == "All entries"


class TestWithValues:
    def test_changing_one_setting_leaves_the_rest_alone(self):
        display = ColumnDisplay(mode="only", only="KM", show_catalogue=False)
        changed = display.with_values(only="H")
        assert changed.only == "H"
        assert changed.mode == "only"
        assert changed.show_catalogue is False

    def test_the_original_is_untouched(self):
        display = ColumnDisplay()
        display.with_values(mode="rank")
        assert display.mode == "all"
