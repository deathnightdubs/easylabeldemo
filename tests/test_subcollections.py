"""Subcollections, per-subcollection labels, and the master view merge."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from numis.errors import NumisError


def test_one_field_two_labels_merges_in_the_master_view(svc, modern, ancients):
    """The requirement: Ruler in one subcollection, Emperor in another, one master column.

    The merge works because it is the same field, not because the names or types match.
    """
    head = svc.create_field("head_of_state", "Head of state", "text")
    svc.show_field(modern, head, display_label="Ruler", show_in_table=True)
    svc.show_field(ancients, head, display_label="Emperor", show_in_table=True)

    assert [(c.key, c.label) for c in svc.columns_for(modern)] == [("head_of_state", "Ruler")]
    assert [(c.key, c.label) for c in svc.columns_for(ancients)] == [("head_of_state", "Emperor")]

    master = svc.master_columns([modern, ancients])
    assert [(c.key, c.label) for c in master] == [("head_of_state", "Head of state")]


def test_distinct_fields_stay_distinct_in_the_master_view(svc, modern, ancients):
    ruler = svc.create_field("ruler", "Ruler", "text")
    emperor = svc.create_field("emperor", "Emperor", "text")
    svc.show_field(modern, ruler, show_in_table=True)
    svc.show_field(ancients, emperor, show_in_table=True)

    keys = [c.key for c in svc.master_columns([modern, ancients])]
    assert sorted(keys) == ["emperor", "ruler"]


def test_subcollections_can_have_entirely_different_columns(svc, modern, ancients):
    weight = svc.create_field("weight", "Weight", "weight")
    die_axis = svc.create_field("die_axis", "Die axis", "angle")
    svc.show_field(modern, weight, show_in_table=True)
    svc.show_field(ancients, weight, show_in_table=True)
    svc.show_field(ancients, die_axis, show_in_table=True)

    assert [c.key for c in svc.columns_for(modern)] == ["weight"]
    assert sorted(c.key for c in svc.columns_for(ancients)) == ["die_axis", "weight"]
    assert sorted(c.key for c in svc.master_columns([modern, ancients])) == ["die_axis", "weight"]


def test_display_label_falls_back_to_the_canonical_label(svc, modern):
    field = svc.create_field("mint", "Mint", "text")
    svc.show_field(modern, field, show_in_table=True)
    assert svc.columns_for(modern)[0].label == "Mint"


def test_a_field_cannot_be_added_to_one_subcollection_twice(svc, modern, session):
    field = svc.create_field("mint", "Mint", "text")
    svc.show_field(modern, field)
    with pytest.raises(IntegrityError):
        svc.show_field(modern, field)
        session.flush()


def test_column_order_follows_the_user(svc, modern):
    first = svc.create_field("a", "A", "text")
    second = svc.create_field("b", "B", "text")
    svc.show_field(modern, second, show_in_table=True, sort_order=1)
    svc.show_field(modern, first, show_in_table=True, sort_order=2)
    assert [c.key for c in svc.columns_for(modern)] == ["b", "a"]


def test_special_blocks_sit_in_the_same_layout(svc, modern):
    field = svc.create_field("mint", "Mint", "text")
    svc.show_field(modern, field, show_in_table=True, sort_order=1)
    svc.show_special_block(modern, "catalogues", display_label="References",
                           sort_order=0, show_in_table=True)
    columns = svc.columns_for(modern)
    assert [(c.key, c.label, c.kind) for c in columns] == [
        ("catalogues", "References", "catalogues"),
        ("mint", "Mint", "field"),
    ]


def test_special_block_kinds_are_checked(svc, modern):
    with pytest.raises(NumisError):
        svc.show_special_block(modern, "nonsense")
    with pytest.raises(NumisError):
        svc.show_special_block(modern, "field")


def test_archived_fields_disappear_from_columns(svc, modern):
    field = svc.create_field("mint", "Mint", "text")
    svc.show_field(modern, field, show_in_table=True)
    svc.archive_field(field)
    assert svc.columns_for(modern) == []


def test_naming_template_renders_the_display_name(svc):
    sub = svc.create_subcollection("World", naming_template="{country} {denom} {date_issued}")
    svc.create_field("country", "Country", "text")
    svc.create_field("denom", "Denomination", "text")
    svc.create_field("date_issued", "Date", "date")
    coin = svc.add_specimen(
        sub, values={"country": "Austria", "denom": "1 Thaler", "date_issued": "1780"}
    )
    assert coin.display_name == "Austria 1 Thaler 1780"


def test_naming_template_tolerates_missing_values(svc):
    sub = svc.create_subcollection("World", naming_template="{country} {denom}")
    svc.create_field("country", "Country", "text")
    svc.create_field("denom", "Denomination", "text")
    coin = svc.add_specimen(sub, values={"country": "Austria"})
    assert coin.display_name == "Austria"
