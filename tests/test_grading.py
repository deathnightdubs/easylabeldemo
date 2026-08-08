"""Grading across incompatible standards, with details grades and stickers."""

from __future__ import annotations

import pytest

from numis.errors import NumisError
from numis.grading import is_problem_grade


def test_a_new_library_has_no_grading_scales(svc):
    """Blank slate: nothing is shipped, so nothing can be mistaken for a decision."""
    from numis.models import GradeLevel, GradeModifier, GradeScale

    for model in (GradeScale, GradeLevel, GradeModifier):
        assert svc.session.query(model).count() == 0


def test_levels_resolve_by_alias(svc, sheldon):
    coin = svc.add_specimen(svc.create_subcollection("US"), display_name="Morgan")
    for spelling in ("MS63", "MS-63", "MS 63", "ms63"):
        grade = svc.add_grade(coin, sheldon, spelling)
        assert grade.normalised == 63.0


def test_unknown_level_is_refused_by_name(svc, sheldon, modern):
    coin = svc.add_specimen(modern)
    with pytest.raises(NumisError) as info:
        svc.add_grade(coin, sheldon, "MS99")
    assert "MS99" in str(info.value)


class TestSharedAxis:
    @pytest.fixture
    def graded(self, svc, modern, sheldon, adjectival, chinese10, modifiers):
        """The worked example from docs/design/02, Part 4.2."""
        cases = [
            ("MS63 CAC gold", sheldon, "MS63", ["CACGOLD"], "tpg", "NGC", None),
            ("MS63 CAC green", sheldon, "MS63", ["CACG"], "tpg", "NGC", None),
            ("MS63", sheldon, "MS63", [], "tpg", "NGC", None),
            ("MS63 Details", sheldon, "MS63", ["DETAILS"], "tpg", "NGC", "Cleaned"),
            ("MS62", sheldon, "MS62", [], "tpg", "PCGS", None),
            ("AU", adjectival, "AU", [], "self", "me", None),
            ("AU Details", adjectival, "AU", ["DETAILS"], "seller", "dealer", "Scratches"),
            ("8", chinese10, "8", [], "tpg", "GBCA", None),
            ("6", chinese10, "6", [], "self", "me", None),
            ("VF", adjectival, "VF", [], "seller", "dealer", None),
        ]
        grades = []
        for name, scale, level, mods, source, by, detail in cases:
            coin = svc.add_specimen(modern, display_name=name)
            grades.append(
                svc.add_grade(
                    coin, scale, level, modifiers=mods, source=source,
                    assigned_by=by, detail_note=detail, is_primary=True,
                )
            )
        return grades

    def test_three_standards_share_one_ordering(self, graded):
        ordered = sorted(graded, key=lambda g: -g.normalised)
        assert [g.raw_text for g in ordered] == [
            "MS63 CAC gold", "MS63 CAC green", "MS63", "MS63 Details", "MS62",
            "AU", "AU Details", "8", "6", "VF",
        ]

    def test_details_sorts_just_below_its_base_grade(self, graded):
        """The answered decision: below the base grade, not above the next one down."""
        by_text = {g.raw_text: g.normalised for g in graded}
        assert by_text["MS62"] < by_text["MS63 Details"] < by_text["MS63"]
        assert by_text["AU Details"] < by_text["AU"]

    def test_stickers_lift_a_coin_within_its_grade(self, graded):
        by_text = {g.raw_text: g.normalised for g in graded}
        assert by_text["MS63"] < by_text["MS63 CAC green"] < by_text["MS63 CAC gold"]
        assert by_text["MS63 CAC gold"] < 64.0

    def test_at_least_vf_works_across_every_standard(self, svc, graded):
        found = svc.grades_at_least(27.5)
        assert len(found) == len(graded)
        scales = {svc.session.get(type(g.scale), g.grade_scale_id).code for g in found}
        assert scales == {"SHELDON", "ADJ", "CN10"}

    def test_problem_coins_can_be_excluded(self, svc, graded):
        clean = svc.grades_at_least(27.5, exclude_problems=True)
        assert "MS63 Details" not in [g.raw_text for g in clean]
        assert "AU Details" not in [g.raw_text for g in clean]
        assert len(clean) == len(graded) - 2

    def test_the_detail_is_available_separately_from_the_grade(self, graded):
        details = {g.raw_text: g.detail_note for g in graded if is_problem_grade(g)}
        assert details == {"MS63 Details": "Cleaned", "AU Details": "Scratches"}


class TestMultipleGrades:
    def test_a_coin_may_hold_grades_from_several_sources(self, svc, modern, sheldon, adjectival):
        coin = svc.add_specimen(modern, display_name="Morgan")
        svc.add_grade(coin, sheldon, "MS63", source="tpg", assigned_by="NGC", is_primary=True)
        svc.add_grade(coin, adjectival, "AU", source="seller", assigned_by="dealer")
        assert len(coin.grades) == 2

    def test_the_primary_grade_is_chosen_by_the_user(self, svc, modern, sheldon, adjectival):
        """Never inferred from recency or from the authority of the source."""
        coin = svc.add_specimen(modern, display_name="Morgan")
        tpg = svc.add_grade(coin, sheldon, "MS63", source="tpg", is_primary=True)
        dealer = svc.add_grade(coin, adjectival, "AU", source="seller")

        assert svc.primary_grade(coin) is tpg  # not changed by adding a newer grade
        svc.set_primary_grade(coin, dealer)
        assert svc.primary_grade(coin) is dealer
        assert tpg.is_primary == 0

    def test_only_one_grade_can_be_primary(self, svc, modern, sheldon):
        coin = svc.add_specimen(modern)
        first = svc.add_grade(coin, sheldon, "MS63", is_primary=True)
        second = svc.add_grade(coin, sheldon, "MS62", is_primary=True)
        assert (first.is_primary, second.is_primary) == (0, 1)

    def test_a_grade_from_another_specimen_is_refused(self, svc, modern, sheldon):
        mine = svc.add_specimen(modern)
        theirs = svc.add_specimen(modern)
        grade = svc.add_grade(theirs, sheldon, "MS63")
        with pytest.raises(NumisError):
            svc.set_primary_grade(mine, grade)


def test_raw_text_records_exactly_what_was_entered(svc, modern, sheldon, modifiers):
    coin = svc.add_specimen(modern)
    grade = svc.add_grade(coin, sheldon, "MS63", modifiers=["CACG"])
    assert grade.raw_text == "MS63 CAC green"


def test_user_can_define_any_scale_they_like(svc, modern):
    """A scale is data, so an unusual local standard needs no code change."""
    scale = svc.create_grade_scale("SPANISH", "Spanish market")
    svc.add_grade_level(scale, "SC", 62.0)
    svc.add_grade_level(scale, "EBC", 45.0)
    coin = svc.add_specimen(modern)
    grade = svc.add_grade(coin, scale, "EBC")
    assert grade.normalised == 45.0
