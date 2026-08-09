"""Grading: typed grades, calculated values, and how a grade reads."""

from __future__ import annotations

import pytest

from numis.errors import NumisError
from numis.grading import GradeDisplay, is_problem_grade, render, suggest_base_value


def test_a_new_library_has_no_grading_data(svc):
    """Blank slate: nothing shipped, so nothing can be mistaken for a decision."""
    from numis.models import GradeLevel, GradeModifier, GradeScale

    for model in (GradeScale, GradeLevel, GradeModifier):
        assert svc.session.query(model).count() == 0


class TestTypedGrades:
    def test_a_grade_is_typed_with_what_it_is_worth(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
        assert grade.grade_label == "MS63"
        assert grade.base_value == 63.0
        assert grade.normalised == 63.0

    def test_the_same_grade_can_be_used_on_many_coins(self, svc, modern, sheldon):
        """The old model kept a registry of grades, and a repeat was a constraint violation
        that took the application down. Nothing is registered now, so this is unremarkable."""
        for _ in range(3):
            coin = svc.add_specimen(modern)
            grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
            assert grade.normalised == 63.0

    def test_a_number_in_the_label_is_offered_as_the_value(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63")
        assert grade.base_value == 63.0

    def test_a_grade_with_no_number_asks_rather_than_inventing_one(self):
        assert suggest_base_value("gVF") is None
        assert suggest_base_value("MS63") == 63.0
        assert suggest_base_value("8") == 8.0

    def test_a_grade_needs_a_label(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        with pytest.raises(NumisError, match="needs a label"):
            svc.add_grade(coin, sheldon, "   ")

    def test_a_grade_can_have_no_scale_at_all(self, svc, modern):
        """Somebody's own opinion does not belong to a published standard."""
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, None, "about VF", base_value=25.0, source="self")
        assert grade.grade_scale_id is None
        assert grade.normalised == 25.0

    def test_editing_a_grade_recalculates_it(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
        svc.update_grade(grade, grade_label="MS64", base_value=64.0, modifiers=[("PLUS", None)])
        assert grade.grade_label == "MS64"
        assert grade.normalised == pytest.approx(64.25)
        assert grade.raw_text == "MS64+"


class TestCalculatedValue:
    def test_modifiers_add_up(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(
            coin, sheldon, "MS63", base_value=63.0, modifiers=[("FB", None), ("CAC", "Gold")]
        )
        assert grade.normalised == pytest.approx(63.0 + 0.15 + 0.15)

    def test_details_sits_just_below_its_base_grade(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        clean = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
        other = svc.add_specimen(modern)
        problem = svc.add_grade(
            other, sheldon, "MS63", base_value=63.0,
            modifiers=[("DETAILS", "Harshly Cleaned")],
        )
        assert 62.0 < problem.normalised < clean.normalised

    def test_a_grade_with_no_value_is_not_comparable(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "gVF", base_value=None)
        assert grade.normalised is None

    def test_incompatible_standards_share_one_order(
        self, svc, modern, sheldon, adjectival, chinese10, modifiers
    ):
        """The worked example: three standards, stickers and problem grades, one ordering."""
        cases = [
            (sheldon, "MS63", 63.0, [("CAC", "Gold")]),
            (sheldon, "MS63", 63.0, [("CAC", "Green")]),
            (sheldon, "MS63", 63.0, []),
            (sheldon, "MS63", 63.0, [("DETAILS", "Cleaned")]),
            (sheldon, "MS62", 62.0, []),
            (adjectival, "AU", 53.0, []),
            (adjectival, "AU", 53.0, [("DETAILS", "Scratches")]),
            (chinese10, "8", 50.0, []),
            (chinese10, "6", 35.0, []),
            (adjectival, "VF", 27.5, []),
        ]
        grades = []
        for scale, label, value, mods in cases:
            coin = svc.add_specimen(modern)
            grades.append(
                svc.add_grade(coin, scale, label, base_value=value, modifiers=mods)
            )

        ordered = sorted(grades, key=lambda g: -g.normalised)
        assert [render(g) for g in ordered] == [
            "MS63 CAC",
            "MS63 CAC",
            "MS63",
            "MS63 Details",
            "MS62",
            "AU",
            "AU Details",
            "8",
            "6",
            "VF",
        ]

    def test_at_least_vf_works_across_standards(self, svc, modern, sheldon, chinese10):
        for scale, label, value in ((sheldon, "MS63", 63.0), (chinese10, "4", 20.0)):
            svc.add_grade(svc.add_specimen(modern), scale, label, base_value=value)
        assert len(svc.grades_at_least(27.5)) == 1

    def test_problem_coins_can_be_excluded(self, svc, modern, sheldon, modifiers):
        svc.add_grade(svc.add_specimen(modern), sheldon, "MS63", base_value=63.0)
        svc.add_grade(
            svc.add_specimen(modern), sheldon, "MS63", base_value=63.0,
            modifiers=[("DETAILS", "Cleaned")],
        )
        assert len(svc.grades_at_least(0)) == 2
        assert len(svc.grades_at_least(0, exclude_problems=True)) == 1


class TestHowAGradeReads:
    def _grade(self, svc, modern, scale, label, value, mods):
        return svc.add_grade(
            svc.add_specimen(modern), scale, label, base_value=value, modifiers=mods
        )

    def test_a_plus_attaches_with_no_space(self, svc, modern, sheldon, modifiers):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("PLUS", None)])
        assert render(grade) == "MS63+"

    def test_a_star_attaches_with_no_space(self, svc, modern, sheldon, modifiers):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("STAR", None)])
        assert render(grade) == "MS63*"

    def test_other_modifiers_are_separated_and_abbreviated(
        self, svc, modern, sheldon, modifiers
    ):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("FB", None), ("BN", None)])
        assert render(grade) == "MS63 FB BN"

    def test_modifiers_read_in_a_settled_order(self, svc, modern, sheldon, modifiers):
        """So two coins with the same modifiers never show them differently."""
        one = self._grade(svc, modern, sheldon, "MS63", 63.0, [("BN", None), ("FB", None)])
        two = self._grade(svc, modern, sheldon, "MS63", 63.0, [("FB", None), ("BN", None)])
        assert render(one) == render(two)

    def test_modifiers_can_be_hidden_entirely(self, svc, modern, sheldon, modifiers):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("FB", None)])
        assert render(grade, GradeDisplay(modifiers=False)) == "MS63"

    def test_a_sticker_shows_its_issuer_then_its_value(self, svc, modern, sheldon, modifiers):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("CAC", "Gold")])
        assert render(grade) == "MS63 CAC"
        assert render(grade, GradeDisplay(modifier_details=True)) == "MS63 CAC Gold"

    def test_details_names_the_problem_only_when_asked(self, svc, modern, adjectival, modifiers):
        grade = self._grade(
            svc, modern, adjectival, "AU", 53.0, [("DETAILS", "Harshly Cleaned")]
        )
        assert render(grade) == "AU Details"
        assert render(grade, GradeDisplay(modifier_details=True)) == "AU Details — Harshly Cleaned"

    def test_the_scale_source_and_grader_can_be_shown(self, svc, modern, sheldon):
        grade = svc.add_grade(
            svc.add_specimen(modern), sheldon, "MS63", base_value=63.0,
            source="tpg", assigned_by="NGC",
        )
        assert render(grade, GradeDisplay(scale=True)) == "MS63 [SHELDON]"
        assert render(grade, GradeDisplay(assigned_by=True)) == "MS63 [NGC]"
        assert "SHELDON" in render(grade, GradeDisplay.full())

    def test_a_grader_can_be_kept_out_of_columns(self, svc, modern, sheldon):
        """A dealer's name on fifty coins is noise once you know it is theirs."""
        grade = svc.add_grade(
            svc.add_specimen(modern), sheldon, "MS63", base_value=63.0,
            assigned_by="Bob Reis", hide_assigned_by=True,
        )
        assert render(grade, GradeDisplay(assigned_by=True)) == "MS63"

        shown = svc.add_grade(
            svc.add_specimen(modern), sheldon, "MS63", base_value=63.0, assigned_by="NGC"
        )
        assert render(shown, GradeDisplay(assigned_by=True)) == "MS63 [NGC]"

    def test_the_cached_text_matches_the_compact_rendering(self, svc, modern, sheldon, modifiers):
        grade = self._grade(svc, modern, sheldon, "MS63", 63.0, [("CAC", "Gold")])
        assert grade.raw_text == render(grade)


class TestSeveralGrades:
    def test_a_coin_may_hold_grades_from_several_sources(self, svc, modern, sheldon, adjectival):
        coin = svc.add_specimen(modern, display_name="Morgan")
        svc.add_grade(coin, sheldon, "MS63", base_value=63.0, source="tpg", assigned_by="NGC")
        svc.add_grade(coin, adjectival, "AU", base_value=53.0, source="seller")
        assert len(svc.grades_for(coin)) == 2

    def test_new_grades_queue_behind_the_first(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        first = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
        second = svc.add_grade(coin, sheldon, "MS62", base_value=62.0)
        assert (first.rank, second.rank) == (1, 2)
        assert svc.primary_grade(coin) is first

    def test_the_order_of_precedence_is_the_users(self, svc, modern, sheldon, adjectival):
        """Never inferred from recency or from the authority of the source."""
        coin = svc.add_specimen(modern)
        tpg = svc.add_grade(coin, sheldon, "MS63", base_value=63.0, source="tpg")
        dealer = svc.add_grade(coin, adjectival, "AU", base_value=53.0, source="seller")
        assert svc.primary_grade(coin) is tpg

        svc.reorder([dealer, tpg])
        assert svc.primary_grade(coin) is dealer
        assert (dealer.rank, tpg.rank) == (1, 2)

    def test_ranks_can_be_set_directly(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0)
        svc.set_rank(grade, 4)
        assert grade.rank == 4
        svc.set_rank(grade, 0)
        assert grade.rank == 1  # never below the front


class TestModifierManagement:
    def test_modifiers_of_every_kind_can_be_defined(self, svc, modifiers):
        kinds = {modifier.kind for modifier in svc.modifiers()}
        assert kinds == {"detail", "sticker", "qualifier", "strike", "colour"}

    def test_an_unknown_kind_is_refused_and_lists_the_options(self, svc):
        with pytest.raises(NumisError, match="unknown modifier kind"):
            svc.create_grade_modifier("X", "X", "nonsense", 0.0)

    def test_a_modifier_can_be_renamed_and_grades_follow(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0, modifiers=[("FB", None)])
        assert grade.raw_text == "MS63 FB"

        svc.update_grade_modifier(modifiers["FB"], abbreviation="FBands")
        assert grade.raw_text == "MS63 FBands"

    def test_changing_the_effect_recalculates_every_grade_using_it(
        self, svc, modern, sheldon, modifiers
    ):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0, modifiers=[("FB", None)])
        assert grade.normalised == pytest.approx(63.15)

        svc.update_grade_modifier(modifiers["FB"], normalised_delta=1.0)
        svc.refresh_grade_text(grade)
        assert grade.normalised == pytest.approx(64.0)

    def test_usage_is_counted_before_deleting(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        svc.add_grade(coin, sheldon, "MS63", base_value=63.0, modifiers=[("FB", None)])
        assert svc.modifier_usage(modifiers["FB"]) == 1
        assert svc.modifier_usage(modifiers["BN"]) == 0

    def test_deleting_an_unused_modifier_is_allowed(self, svc, modifiers):
        assert svc.delete_grade_modifier(modifiers["BN"]) == 0
        assert "BN" not in {modifier.code for modifier in svc.modifiers()}

    def test_deleting_one_in_use_is_refused_unless_forced(self, svc, modern, sheldon, modifiers):
        coin = svc.add_specimen(modern)
        grade = svc.add_grade(coin, sheldon, "MS63", base_value=63.0, modifiers=[("FB", None)])

        with pytest.raises(NumisError, match="used by 1 grade"):
            svc.delete_grade_modifier(modifiers["FB"])

        assert svc.delete_grade_modifier(modifiers["FB"], force=True) == 1
        svc.session.refresh(grade)
        assert grade.raw_text == "MS63"
        assert grade.normalised == pytest.approx(63.0)

    def test_modifiers_can_be_listed_by_kind(self, svc, modifiers):
        assert {m.code for m in svc.modifiers("sticker")} == {"CAC", "WINGS"}


def test_details_are_recorded_per_coin_not_per_modifier(svc, modern, adjectival, modifiers):
    """One Details modifier, different problems on different coins."""
    first = svc.add_grade(
        svc.add_specimen(modern), adjectival, "AU", base_value=53.0,
        modifiers=[("DETAILS", "Harshly Cleaned")],
    )
    second = svc.add_grade(
        svc.add_specimen(modern), adjectival, "XF", base_value=42.5,
        modifiers=[("DETAILS", "Holed")],
    )
    detailed = GradeDisplay(modifier_details=True)
    assert render(first, detailed) == "AU Details — Harshly Cleaned"
    assert render(second, detailed) == "XF Details — Holed"
    assert is_problem_grade(first) and is_problem_grade(second)
