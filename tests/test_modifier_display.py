"""How modifiers read, and the reported faults in that.

Five things were wrong or missing, all reported from a real session:

* a sticker named ``CAC Gold`` read as ``CAC``, because the issuer was used in place of the name;
* "spell out what each modifier says" appeared to do nothing;
* there was no way to show full names instead of short forms;
* the order modifiers append in could not be changed;
* a modifier's own name and what a particular coin says about it were not clearly separate.
"""

from __future__ import annotations

import pytest

from numis import grading
from numis.grading import GradeDisplay, render

SHORT = GradeDisplay()
SAYS = GradeDisplay(modifier_details=True)
FULL = GradeDisplay(modifier_details=True, modifier_full_names=True)


@pytest.fixture
def kit(svc):
    """The modifiers a real slab carries, named as a collector would name them."""
    return {
        "DETAILS": svc.create_grade_modifier("DETAILS", "Details", "detail", -0.4),
        "FB": svc.create_grade_modifier(
            "FB", "Full Bands", "strike", 0.15, abbreviation="FB"
        ),
        "RD": svc.create_grade_modifier("RD", "Red", "colour", 0.0, abbreviation="RD"),
        "CACG": svc.create_grade_modifier(
            "CACG", "CAC Gold", "sticker", 0.15, abbreviation="CAC Gold", issuer="CAC"
        ),
        "PLUS": svc.create_grade_modifier(
            "PLUS", "+", "qualifier", 0.25, attach_without_space=True
        ),
    }


def graded(svc, modern, sheldon, label="MS", base=60.0, modifiers=()):
    coin = svc.add_specimen(modern)
    return svc.add_grade(coin, sheldon, label, base_value=base, modifiers=list(modifiers))


class TestAStickerReadsByItsOwnName:
    """Reported: a sticker with display and name 'CAC Gold' displayed only 'CAC'."""

    def test_the_name_the_user_gave_it_is_what_shows(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", None)])
        assert render(grade) == "MS CAC Gold"

    def test_the_issuer_no_longer_replaces_the_name(self, svc, modern, sheldon, kit):
        """The old behaviour threw away the part the user chose to type."""
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", None)])
        assert render(grade) != "MS CAC"

    def test_the_company_is_still_recorded(self, svc, kit):
        assert kit["CACG"].issuer == "CAC"

    def test_the_company_can_be_shown_when_the_name_omits_it(self, svc, modern, sheldon):
        gold = svc.create_grade_modifier(
            "GOLD", "Gold sticker", "sticker", 0.15, abbreviation="Gold", issuer="CAC"
        )
        grade = graded(svc, modern, sheldon, modifiers=[(gold, None)])
        assert render(grade) == "MS Gold"
        assert render(grade, GradeDisplay(sticker_issuer=True)) == "MS CAC Gold"

    def test_the_company_is_not_repeated_when_the_name_already_starts_with_it(
        self, svc, modern, sheldon, kit
    ):
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", None)])
        assert render(grade, GradeDisplay(sticker_issuer=True)) == "MS CAC Gold"


class TestSpellingOutWhatEachOneSays:
    """Reported: the option appeared to do nothing.

    It had no effect because the per-coin detail it shows could never be typed — see
    ``tests/ui/test_grade_dialog.py`` for that half.
    """

    def test_it_adds_what_the_coin_records(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Harshly Cleaned")])
        assert render(grade, SHORT) == "MS Details"
        assert render(grade, SAYS) == "MS Details — Harshly Cleaned"

    def test_a_problem_is_separated_by_a_dash(self, svc, modern, sheldon, kit):
        """'Details Harshly Cleaned' would read like the name of a grade."""
        grade = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Holed")])
        assert " — " in render(grade, SAYS)

    def test_everything_else_is_separated_by_a_space(self, svc, modern, sheldon, kit):
        gold = svc.create_grade_modifier("G", "Gold sticker", "sticker", 0.1, abbreviation="Gold")
        grade = graded(svc, modern, sheldon, modifiers=[(gold, "Plus")])
        assert render(grade, SAYS) == "MS Gold Plus"

    def test_a_coin_recording_nothing_extra_is_unaffected(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("FB", None)])
        assert render(grade, SHORT) == render(grade, SAYS) == "MS FB"

    def test_it_is_not_said_twice_when_the_name_already_says_it(self, svc, modern, sheldon, kit):
        """A modifier called 'CAC Gold' on a coin whose sticker says 'Gold'."""
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", "Gold")])
        assert render(grade, SAYS) == "MS CAC Gold"

    def test_the_check_ignores_capitals_and_spacing(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", "  gold ")])
        assert render(grade, SAYS) == "MS CAC Gold"

    def test_a_different_value_is_still_added(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("CACG", "Green")])
        assert render(grade, SAYS) == "MS CAC Gold Green"


class TestFullNamesInsteadOfShortForms:
    """Requested: keep FB and RD, but be able to show Full Bands and Red instead."""

    def test_short_forms_by_default(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("FB", None), ("RD", None)])
        assert render(grade, SHORT) == "MS FB RD"

    def test_full_names_when_asked(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("FB", None), ("RD", None)])
        assert render(grade, GradeDisplay(modifier_full_names=True)) == "MS Full Bands Red"

    def test_a_modifier_with_no_short_form_reads_the_same_either_way(
        self, svc, modern, sheldon, kit
    ):
        grade = graded(svc, modern, sheldon, modifiers=[("DETAILS", None)])
        assert render(grade, SHORT) == render(grade, GradeDisplay(modifier_full_names=True))

    def test_the_three_readings_the_request_described(self, svc, modern, sheldon, kit):
        grade = graded(
            svc,
            modern,
            sheldon,
            modifiers=[("DETAILS", "Harshly Cleaned"), ("FB", None), ("RD", None)],
        )
        svc.reorder_modifiers([kit["DETAILS"], kit["FB"], kit["RD"]])

        assert render(grade, SHORT) == "MS Details FB RD"
        assert render(grade, SAYS) == "MS Details — Harshly Cleaned FB RD"
        assert render(grade, FULL) == "MS Details — Harshly Cleaned Full Bands Red"


class TestAttachingWithoutASpace:
    def test_a_plus_still_attaches_directly(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, "MS63", 63.0, [("PLUS", None)])
        assert render(grade) == "MS63+"

    def test_it_attaches_directly_with_full_names_too(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, "MS63", 63.0, [("PLUS", None)])
        assert render(grade, GradeDisplay(modifier_full_names=True)) == "MS63+"


class TestTheOrderModifiersAppendIn:
    """Requested: be able to change the order they are appended to the grade."""

    def _grade(self, svc, modern, sheldon):
        return graded(
            svc, modern, sheldon, modifiers=[("DETAILS", None), ("FB", None), ("RD", None)]
        )

    def test_the_default_is_the_order_a_slab_reads_in(self, svc, modern, sheldon, kit):
        assert render(self._grade(svc, modern, sheldon)) == "MS FB RD Details"

    def test_the_user_can_put_a_modifier_first(self, svc, modern, sheldon, kit):
        grade = self._grade(svc, modern, sheldon)
        svc.reorder_modifiers([kit["DETAILS"], kit["FB"], kit["RD"]])
        assert render(grade) == "MS Details FB RD"

    def test_reordering_takes_effect_on_every_coin_at_once(self, svc, modern, sheldon, kit):
        """The order belongs to the modifier, not to a coin."""
        first = self._grade(svc, modern, sheldon)
        second = self._grade(svc, modern, sheldon)
        svc.reorder_modifiers([kit["RD"], kit["DETAILS"], kit["FB"]])
        assert render(first) == render(second) == "MS RD Details FB"

    def test_two_coins_with_the_same_modifiers_never_read_them_differently(
        self, svc, modern, sheldon, kit
    ):
        """The invariant reordering must not break, whichever order they were entered in."""
        one = graded(svc, modern, sheldon, modifiers=[("RD", None), ("FB", None)])
        two = graded(svc, modern, sheldon, modifiers=[("FB", None), ("RD", None)])
        assert render(one) == render(two)

        svc.reorder_modifiers([kit["RD"], kit["FB"]])
        assert render(one) == render(two) == "MS RD FB"

    def test_the_cached_text_is_rebuilt(self, svc, modern, sheldon, kit):
        """The grid reads raw_text, so a stale one would leave the old order on screen."""
        grade = self._grade(svc, modern, sheldon)
        svc.reorder_modifiers([kit["DETAILS"], kit["FB"], kit["RD"]])
        assert grade.raw_text == "MS Details FB RD"

    def test_modifiers_not_given_an_order_keep_their_kind_s_place(
        self, svc, modern, sheldon, kit
    ):
        grade = self._grade(svc, modern, sheldon)
        svc.reorder_modifiers([kit["DETAILS"]])
        assert render(grade) == "MS Details FB RD"

    def test_the_order_can_be_reset(self, svc, modern, sheldon, kit):
        grade = self._grade(svc, modern, sheldon)
        svc.reorder_modifiers([kit["DETAILS"], kit["RD"], kit["FB"]])
        assert render(grade) == "MS Details RD FB"

        svc.clear_modifier_order()
        assert render(grade) == "MS FB RD Details"

    def test_the_management_list_reads_in_the_same_order_as_a_grade(
        self, svc, modern, sheldon, kit
    ):
        svc.reorder_modifiers([kit["DETAILS"], kit["RD"], kit["FB"]])
        listed = [m.code for m in svc.modifiers_in_reading_order()]
        assert listed[:3] == ["DETAILS", "RD", "FB"]

    def test_a_new_modifier_goes_where_its_kind_belongs(self, svc, modern, sheldon, kit):
        svc.reorder_modifiers([kit["DETAILS"]])
        cameo = svc.create_grade_modifier("CAM", "Cameo", "contrast", 0.1, abbreviation="CAM")
        grade = graded(svc, modern, sheldon, modifiers=[("DETAILS", None), (cameo, None)])
        assert render(grade) == "MS Details CAM"


class TestOneDefinitionManyCoins:
    """Reported: modifiers should be handled per coin, not globally.

    The definition is shared — one ``Details`` covers every kind of problem — while what each
    coin says about it is recorded against that coin. Without this, a user needs a new global
    modifier for every problem any coin has.
    """

    def test_one_definition_carries_different_details_per_coin(
        self, svc, modern, sheldon, kit
    ):
        cleaned = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Harshly Cleaned")])
        holed = graded(svc, modern, sheldon, "AU", 53.0, [("DETAILS", "Holed")])

        assert render(cleaned, SAYS) == "MS Details — Harshly Cleaned"
        assert render(holed, SAYS) == "AU Details — Holed"
        assert len(svc.modifiers("detail")) == 1

    def test_the_value_is_the_same_whatever_the_coin_says(self, svc, modern, sheldon, kit):
        """The wording is presentation; the arithmetic belongs to the definition."""
        cleaned = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Harshly Cleaned")])
        holed = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Holed")])
        assert cleaned.normalised == holed.normalised == pytest.approx(59.6)

    def test_editing_the_definition_changes_every_coin(self, svc, modern, sheldon, kit):
        grade = graded(svc, modern, sheldon, modifiers=[("FB", None)])
        svc.update_grade_modifier(kit["FB"], abbreviation="FBd")
        assert render(grade) == "MS FBd"

    def test_the_per_coin_detail_survives_editing_the_definition(
        self, svc, modern, sheldon, kit
    ):
        grade = graded(svc, modern, sheldon, modifiers=[("DETAILS", "Harshly Cleaned")])
        svc.update_grade_modifier(kit["DETAILS"], abbreviation="Det")
        assert render(grade, SAYS) == "MS Det — Harshly Cleaned"


class TestRenderingWithoutAGrade:
    """The grade dialog previews a grade that has not been saved yet."""

    def test_pieces_can_be_assembled_from_modifiers_and_details(self, svc, kit):
        pairs = [(kit["FB"], None), (kit["DETAILS"], "Harshly Cleaned")]
        assert grading.assemble("MS", pairs) == "MS FB Details"
        assert grading.assemble("MS", pairs, SAYS) == "MS FB Details — Harshly Cleaned"

    def test_the_preview_orders_them_as_a_column_would(self, svc, kit):
        """Given in the wrong order on purpose: the reading order must not depend on that."""
        pairs = [(kit["DETAILS"], None), (kit["RD"], None), (kit["FB"], None)]
        assert grading.assemble("MS", pairs) == "MS FB RD Details"

    def test_an_empty_grade_assembles_to_nothing(self, svc, kit):
        assert grading.assemble("", []) == ""

    def test_modifiers_can_be_left_out_entirely(self, svc, kit):
        pairs = [(kit["FB"], None)]
        assert grading.assemble("MS", pairs, GradeDisplay(modifiers=False)) == "MS"
