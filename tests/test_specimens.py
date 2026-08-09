"""Specimens: one row per coin, bulk operations, and the Trash."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from numis.errors import NumisError
from numis.models import Specimen


def test_there_is_no_quantity_column(svc, modern):
    """One row is one coin, so collection totals are always a plain row count."""
    assert not hasattr(Specimen, "quantity")


def test_bulk_add_creates_separate_rows(svc, modern):
    """This is what replaces inheritance, since there is no coin-type layer."""
    svc.create_field("ruler", "Ruler", "text")
    made = svc.bulk_add(modern, 47, values={"ruler": "Victoria"})

    assert len(made) == 47
    assert len({coin.id for coin in made}) == 47
    assert svc.session.query(Specimen).count() == 47
    assert all(svc.display(coin, "ruler") == "Victoria" for coin in made)


def test_bulk_added_rows_are_independent(svc, modern):
    svc.create_field("note", "Note", "text")
    first, second = svc.bulk_add(modern, 2, values={"note": "shared"})
    svc.set_value(first, "note", "changed")
    assert svc.display(first, "note") == "changed"
    assert svc.display(second, "note") == "shared"


def test_bulk_add_rejects_a_nonsense_count(svc, modern):
    with pytest.raises(NumisError):
        svc.bulk_add(modern, 0)


def test_bulk_edit_applies_to_every_selected_row(svc, modern):
    svc.create_field("grade_note", "Grade note", "text")
    coins = svc.bulk_add(modern, 5)
    changed = svc.bulk_edit(coins, {"grade_note": "as struck"})
    assert changed == 5
    assert all(svc.display(coin, "grade_note") == "as struck" for coin in coins)


def test_bulk_edit_of_an_unknown_field_is_refused_by_name(svc, modern):
    coins = svc.bulk_add(modern, 2)
    with pytest.raises(NumisError) as info:
        svc.bulk_edit(coins, {"nonexistent": "x"})
    assert "nonexistent" in str(info.value)


class TestTrash:
    def test_soft_delete_hides_but_keeps(self, svc, modern):
        coin = svc.add_specimen(modern, display_name="Thaler")
        svc.soft_delete(coin)

        assert coin.deleted_at is not None
        assert list(svc.session.scalars(svc.live_specimens(modern))) == []
        assert svc.session.get(Specimen, coin.id) is not None

    def test_restore_brings_it_back(self, svc, modern):
        coin = svc.add_specimen(modern, display_name="Thaler")
        svc.soft_delete(coin)
        svc.restore(coin)
        assert [c.display_name for c in svc.session.scalars(svc.live_specimens(modern))] == [
            "Thaler"
        ]

    def test_there_is_no_automatic_purge(self, svc, modern):
        """Retained indefinitely until the user asks; see docs/design/01, Part 1.7."""
        coin = svc.add_specimen(modern)
        svc.soft_delete(coin)
        svc.reindex_all()
        assert svc.session.get(Specimen, coin.id) is not None

    def test_purge_cascades_to_everything_owned_by_the_coin(self, svc, modern, sheldon):
        from numis.models import CatalogReference, ExternalLink, SpecimenEvent, SpecimenGrade

        svc.create_field("note", "Note", "text")
        km = svc.create_catalog("KM", "Krause")
        coin = svc.add_specimen(modern, values={"note": "keeper"})
        svc.add_reference(coin, km, "2073")
        svc.add_grade(coin, sheldon, "MS63", rank=1)
        svc.add_link(coin, "https://example.invalid/record")
        svc.add_event(coin, "acquired", amount="10.00")

        svc.purge(coin)

        for model in (CatalogReference, SpecimenGrade, ExternalLink, SpecimenEvent):
            assert svc.session.query(model).count() == 0


def test_inventory_codes_are_unique_when_used(svc, modern, session):
    svc.add_specimen(modern, inventory_code="A-001")
    with pytest.raises(IntegrityError):
        svc.add_specimen(modern, inventory_code="A-001")
        session.flush()


def test_inventory_codes_are_optional_and_many_may_be_absent(svc, modern):
    """A partial unique index, so NULLs do not collide with each other."""
    svc.add_specimen(modern)
    svc.add_specimen(modern)
    assert svc.session.query(Specimen).count() == 2


def test_a_subcollection_with_specimens_cannot_be_deleted(svc, modern, session):
    svc.add_specimen(modern)
    session.delete(modern)
    with pytest.raises(IntegrityError):
        session.flush()


def test_external_links_record_where_a_coin_is_documented(svc, modern):
    coin = svc.add_specimen(modern)
    svc.add_link(coin, "https://zeno.ru/showphoto.php?photo=12345", kind="zeno", label="Zeno")
    svc.add_link(coin, "https://example.invalid/lot/812", kind="auction", reference="lot 812")
    assert [(link.kind, link.reference) for link in coin.links] == [
        ("zeno", None),
        ("auction", "lot 812"),
    ]
