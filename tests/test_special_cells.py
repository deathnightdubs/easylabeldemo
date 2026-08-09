"""What a catalogue, grade, certification or links column actually shows.

The three ways of choosing entries — all, only one source, only one rank — applied to each of the
four systems, plus the per-system "how much of each" options. No GUI: the rules live in the
service so they can be tested here and reused by exports.
"""

from __future__ import annotations

import pytest

from numis.columns import ColumnDisplay


@pytest.fixture
def coin(svc, modern):
    return svc.add_specimen(modern, display_name="Test coin")


def cell(svc, specimen, kind, **settings):
    return svc.special_cell(specimen, kind, ColumnDisplay(**settings))


# ---------------------------------------------------------------------------
# Catalogue numbers
# ---------------------------------------------------------------------------


@pytest.fixture
def catalogued(svc, coin):
    """Three catalogue numbers, in the order the user put them."""
    krause = svc.create_catalog("KM", "Krause")
    hartill = svc.create_catalog("H", "Hartill")
    numista = svc.create_catalog("N#", "Numista")
    svc.add_reference(coin, krause, "1866", rank=1)
    svc.add_reference(coin, hartill, "1.01", rank=2)
    svc.add_reference(coin, numista, "12345", rank=3)
    return coin


class TestCatalogueColumn:
    def test_all_of_them_read_in_the_user_s_order(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues") == "KM 1866 · H 1.01 · N# 12345"

    def test_only_one_catalogue_makes_a_column_about_that_catalogue(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", mode="only", only="H") == "H 1.01"

    def test_the_catalogue_code_can_be_dropped_when_it_is_the_same_every_row(
        self, svc, catalogued
    ):
        """A "Hartill" column does not need to say Hartill on all four hundred rows."""
        assert (
            cell(svc, catalogued, "catalogues", mode="only", only="H", show_catalogue=False)
            == "1.01"
        )

    def test_the_filter_ignores_capitalisation(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", mode="only", only="h") == "H 1.01"

    def test_a_catalogue_the_coin_is_not_in_leaves_the_cell_blank(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", mode="only", only="RIC") == ""

    def test_rank_shows_the_one_the_user_put_first(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", mode="rank", rank=1) == "KM 1866"
        assert cell(svc, catalogued, "catalogues", mode="rank", rank=2) == "H 1.01"
        assert cell(svc, catalogued, "catalogues", mode="rank", rank=3) == "N# 12345"

    def test_asking_for_a_fourth_when_there_are_three_is_blank(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", mode="rank", rank=4) == ""

    def test_reordering_in_the_details_changes_what_a_rank_column_shows(self, svc, catalogued):
        references = svc.references_for(catalogued)
        svc.reorder([references[2], references[0], references[1]])
        assert cell(svc, catalogued, "catalogues", mode="rank", rank=1) == "N# 12345"

    def test_the_separator_is_the_user_s_own(self, svc, catalogued):
        assert cell(svc, catalogued, "catalogues", separator=" / ") == (
            "KM 1866 / H 1.01 / N# 12345"
        )

    def test_a_coin_with_no_numbers_is_blank_in_every_mode(self, svc, coin):
        for settings in ({}, {"mode": "only", "only": "KM"}, {"mode": "rank", "rank": 1}):
            assert cell(svc, coin, "catalogues", **settings) == ""


# ---------------------------------------------------------------------------
# Grades
# ---------------------------------------------------------------------------


@pytest.fixture
def graded(svc, coin, sheldon, modifiers):
    """Two grades: a stickered one from PCGS, and a dealer's own opinion behind it."""
    svc.add_grade(
        coin, sheldon, "MS63", base_value=63.0,
        modifiers=[("CAC", "Gold")], source="tpg", assigned_by="PCGS", rank=1,
    )
    svc.add_grade(
        coin, sheldon, "MS62", base_value=62.0,
        source="seller", assigned_by="Bob Reis", rank=2,
    )
    return coin


class TestGradeColumn:
    def test_all_of_them_read_in_order(self, svc, graded):
        assert cell(svc, graded, "grades") == "MS63 CAC · MS62"

    def test_modifiers_can_be_hidden_for_a_narrow_column(self, svc, graded):
        assert cell(svc, graded, "grades", show_modifiers=False) == "MS63 · MS62"

    def test_modifiers_can_be_spelled_out(self, svc, graded):
        assert cell(svc, graded, "grades", modifier_details=True) == "MS63 CAC Gold · MS62"

    def test_only_one_grader_makes_a_column_about_that_grader(self, svc, graded):
        assert cell(svc, graded, "grades", mode="only", only="PCGS") == "MS63 CAC"
        assert cell(svc, graded, "grades", mode="only", only="Bob Reis") == "MS62"

    def test_a_grade_can_also_be_filtered_by_where_it_came_from(self, svc, graded):
        """'tpg' and 'seller' are the sources; a company name is who assigned it."""
        assert cell(svc, graded, "grades", mode="only", only="tpg") == "MS63 CAC"
        assert cell(svc, graded, "grades", mode="only", only="seller") == "MS62"

    def test_rank_shows_the_headline_grade(self, svc, graded):
        assert cell(svc, graded, "grades", mode="rank", rank=1) == "MS63 CAC"
        assert cell(svc, graded, "grades", mode="rank", rank=2) == "MS62"

    def test_the_scale_can_be_shown(self, svc, graded):
        assert cell(svc, graded, "grades", mode="rank", rank=1, show_scale=True) == (
            "MS63 CAC [SHELDON]"
        )

    def test_who_assigned_it_can_be_shown(self, svc, graded):
        assert cell(svc, graded, "grades", mode="rank", rank=1, show_assigned_by=True) == (
            "MS63 CAC [PCGS]"
        )

    def test_the_source_can_be_shown(self, svc, graded):
        assert cell(svc, graded, "grades", mode="rank", rank=2, show_source=True) == (
            "MS62 [seller]"
        )

    def test_a_grade_can_opt_out_of_showing_who_assigned_it(self, svc, coin, sheldon):
        """Requested: record that a dealer graded a hundred coins without a column of his name."""
        svc.add_grade(
            coin, sheldon, "VF30", base_value=30.0,
            assigned_by="Bob Reis", hide_assigned_by=True, rank=1,
        )
        shown = cell(svc, coin, "grades", show_assigned_by=True)
        assert shown == "VF30"
        assert "Bob Reis" not in shown

    def test_hiding_it_on_one_grade_does_not_hide_it_on_another(self, svc, coin, sheldon):
        svc.add_grade(
            coin, sheldon, "VF30", base_value=30.0,
            assigned_by="Bob Reis", hide_assigned_by=True, rank=1,
        )
        svc.add_grade(coin, sheldon, "VF35", base_value=35.0, assigned_by="NGC", rank=2)
        assert cell(svc, coin, "grades", show_assigned_by=True) == "VF30 · VF35 [NGC]"

    def test_a_grade_still_opted_out_is_findable_by_its_grader(self, svc, coin, sheldon):
        """Hiding the name from the column must not hide the grade from a filter."""
        svc.add_grade(
            coin, sheldon, "VF30", base_value=30.0,
            assigned_by="Bob Reis", hide_assigned_by=True, rank=1,
        )
        assert cell(svc, coin, "grades", mode="only", only="Bob Reis") == "VF30"


class TestGradeColumnAndTheCalculatedValue:
    def test_the_displayed_grade_is_what_the_user_typed(self, svc, coin, sheldon, modifiers):
        """Display and comparison are separate: the label reads, the calculated value sorts."""
        grade = svc.add_grade(
            coin, sheldon, "MS63", base_value=63.0, modifiers=[("PLUS", None)], rank=1
        )
        assert cell(svc, coin, "grades") == "MS63+"
        assert grade.normalised == pytest.approx(63.25)


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------


@pytest.fixture
def certified(svc, coin):
    ngc = svc.create_grading_company("NGC", "NGC")
    pcgs = svc.create_grading_company("PCGS", "PCGS")
    svc.add_certification(coin, ngc, cert_number="2871554-013", rank=1)
    svc.add_certification(coin, pcgs, cert_number="44556677", rank=2)
    return coin


class TestCertificationColumn:
    def test_all_of_them_read_in_order(self, svc, certified):
        assert cell(svc, certified, "certifications") == "NGC 2871554-013 · PCGS 44556677"

    def test_only_one_company(self, svc, certified):
        assert (
            cell(svc, certified, "certifications", mode="only", only="PCGS") == "PCGS 44556677"
        )

    def test_rank_shows_the_headline_certification(self, svc, certified):
        assert (
            cell(svc, certified, "certifications", mode="rank", rank=1) == "NGC 2871554-013"
        )

    def test_the_order_is_the_user_s_and_does_not_wander(self, svc, certified):
        """It had no ordering at all, so SQLite decided — and could decide differently."""
        first = [cell(svc, certified, "certifications") for _ in range(3)]
        assert len(set(first)) == 1

    def test_a_cracked_out_certification_drops_out_of_the_column(self, svc, certified):
        certifications = svc.current_certifications(certified)
        certifications[0].status = "cracked_out"
        svc.session.flush()
        assert cell(svc, certified, "certifications") == "PCGS 44556677"

    def test_a_certification_with_no_number_still_shows_its_company(self, svc, coin):
        """An endorsement often has no number of its own."""
        cac = svc.create_grading_company("CAC", "CAC")
        svc.add_certification(coin, cac, rank=1)
        assert cell(svc, coin, "certifications") == "CAC"


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


@pytest.fixture
def linked(svc, coin):
    svc.add_link(coin, "https://zeno.ru/showphoto.php?photo=1", kind="zeno", label="Zeno 1")
    svc.add_link(coin, "https://en.numista.com/x", kind="numista", label="Numista")
    return coin


class TestLinkColumn:
    def test_links_are_counted_by_default(self, svc, linked):
        assert cell(svc, linked, "links") == "2"

    def test_they_can_be_listed_instead(self, svc, linked):
        assert cell(svc, linked, "links", show_labels=True) == "Zeno 1 · Numista"

    def test_a_count_of_one_kind_only(self, svc, linked):
        assert cell(svc, linked, "links", mode="only", only="zeno") == "1"

    def test_listing_one_kind_only(self, svc, linked):
        assert (
            cell(svc, linked, "links", mode="only", only="numista", show_labels=True)
            == "Numista"
        )

    def test_no_links_is_blank_rather_than_zero(self, svc, coin):
        """A column of noughts is noise; blank means nothing to follow."""
        assert cell(svc, coin, "links") == ""

    def test_a_kind_the_coin_has_none_of_is_blank(self, svc, linked):
        assert cell(svc, linked, "links", mode="only", only="auction") == ""

    def test_a_link_with_no_label_falls_back_to_its_address(self, svc, coin):
        svc.add_link(coin, "https://example.invalid/a", kind="other")
        assert cell(svc, coin, "links", show_labels=True) == "https://example.invalid/a"


# ---------------------------------------------------------------------------
# Storage on the column itself
# ---------------------------------------------------------------------------


class TestSettingsAreRemembered:
    def test_a_column_starts_with_the_defaults(self, svc, modern):
        block = svc.show_special_block(modern, "grades", show_in_table=True)
        assert svc.block_display(block) == ColumnDisplay()

    def test_settings_survive_being_saved_and_read_back(self, svc, modern):
        block = svc.show_special_block(modern, "catalogues", show_in_table=True)
        svc.set_block_display(block, ColumnDisplay(mode="only", only="H", show_catalogue=False))

        reread = svc.block_display(svc.block_for(modern, "catalogues"))
        assert reread.mode == "only"
        assert reread.only == "H"
        assert reread.show_catalogue is False

    def test_the_column_the_grid_builds_carries_them(self, svc, modern):
        block = svc.show_special_block(modern, "grades", show_in_table=True)
        svc.set_block_display(block, ColumnDisplay(mode="rank", rank=2, show_scale=True))

        column = next(c for c in svc.columns_for(modern) if c.kind == "grades")
        assert column.display.mode == "rank"
        assert column.display.rank == 2
        assert column.display.show_scale is True

    def test_looking_up_a_block_that_is_not_placed_gives_nothing(self, svc, modern):
        assert svc.block_for(modern, "links") is None

    def test_a_field_block_is_not_mistaken_for_a_special_one(self, svc, modern):
        field = svc.create_field("ruler", "Ruler", "text")
        svc.show_field(modern, field, show_in_table=True)
        assert svc.block_for(modern, "field") is None

    def test_settings_belong_to_one_subcollection_at_a_time(self, svc, modern, ancients):
        for subcollection in (modern, ancients):
            svc.show_special_block(subcollection, "catalogues", show_in_table=True)
        svc.set_block_display(
            svc.block_for(modern, "catalogues"), ColumnDisplay(mode="only", only="H")
        )
        assert svc.block_display(svc.block_for(ancients, "catalogues")) == ColumnDisplay()


class TestTheMasterView:
    def test_agreeing_subcollections_keep_their_settings(self, svc, modern, ancients):
        chosen = ColumnDisplay(mode="rank", rank=2)
        for subcollection in (modern, ancients):
            block = svc.show_special_block(subcollection, "grades", show_in_table=True)
            svc.set_block_display(block, chosen)

        column = next(c for c in svc.master_columns([modern, ancients]) if c.kind == "grades")
        assert column.display == chosen

    def test_disagreeing_subcollections_fall_back_to_the_defaults(self, svc, modern, ancients):
        """Neither one's settings are more correct, so the merge picks neither."""
        first = svc.show_special_block(modern, "grades", show_in_table=True)
        second = svc.show_special_block(ancients, "grades", show_in_table=True)
        svc.set_block_display(first, ColumnDisplay(mode="only", only="NGC"))
        svc.set_block_display(second, ColumnDisplay(mode="rank", rank=3))

        column = next(c for c in svc.master_columns([modern, ancients]) if c.kind == "grades")
        assert column.display == ColumnDisplay()

    def test_a_column_only_one_subcollection_has_keeps_its_settings(self, svc, modern, ancients):
        block = svc.show_special_block(modern, "links", show_in_table=True)
        svc.set_block_display(block, ColumnDisplay(show_labels=True))

        column = next(c for c in svc.master_columns([modern, ancients]) if c.kind == "links")
        assert column.display.show_labels is True


class TestUnknownKinds:
    def test_a_kind_with_no_renderer_is_blank_rather_than_an_error(self, svc, coin):
        """'history' is a legal block kind with nothing to show in one cell yet."""
        assert svc.special_cell(coin, "history") == ""

    def test_no_settings_at_all_means_the_defaults(self, svc, catalogued):
        assert svc.special_cell(catalogued, "catalogues") == "KM 1866 · H 1.01 · N# 12345"
