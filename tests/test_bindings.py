"""Feature bindings: the user tells a feature which field to use.

This replaces the semantic-role idea from the first draft. Nothing is inferred from a field's
name or type, which is why the label generator's fixed ``diameter_column`` becomes a question
answered once.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from numis.errors import BindingNotSet


def test_an_unset_binding_says_what_it_needs(svc, modern):
    """Never a stack trace and never a silent zero: the interface can offer to fix it."""
    coin = svc.add_specimen(modern)
    with pytest.raises(BindingNotSet) as info:
        svc.bound_value(coin, "labels", "cutout_diameter")

    error = info.value
    assert error.feature == "labels"
    assert error.purpose == "cutout_diameter"
    assert "choose a field" in str(error)


def test_binding_a_field_makes_the_value_available(svc, modern):
    diameter = svc.create_field("diameter", "Diameter", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=diameter)
    coin = svc.add_specimen(modern, values={"diameter": "38.1mm"})
    assert svc.bound_value(coin, "labels", "cutout_diameter") == pytest.approx(38.1)


def test_a_binding_can_point_at_a_constant(svc, modern):
    """'Every coin in this print run is 38.1 mm' needs no field to exist at all."""
    svc.set_binding("labels", "cutout_diameter", constant={"mm": 38.1})
    coin = svc.add_specimen(modern)
    assert svc.bound_value(coin, "labels", "cutout_diameter") == {"mm": 38.1}


def test_a_subcollection_binding_overrides_the_library_default(svc, modern, ancients):
    library_wide = svc.create_field("diameter", "Diameter", "dimension")
    for_ancients = svc.create_field("measured_diameter", "Measured diameter", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=library_wide)
    svc.set_binding("labels", "cutout_diameter", field=for_ancients, subcollection=ancients)

    modern_coin = svc.add_specimen(modern, values={"diameter": "20"})
    ancient_coin = svc.add_specimen(ancients, values={"measured_diameter": "17.5"})

    assert svc.bound_value(modern_coin, "labels", "cutout_diameter") == pytest.approx(20.0)
    assert svc.bound_value(ancient_coin, "labels", "cutout_diameter") == pytest.approx(17.5)


def test_setting_the_same_binding_twice_updates_it(svc):
    first = svc.create_field("a", "A", "dimension")
    second = svc.create_field("b", "B", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=first)
    svc.set_binding("labels", "cutout_diameter", field=second)

    resolved = svc.resolve_binding("labels", "cutout_diameter")
    assert resolved.field_definition_id == second.id


def test_duplicate_bindings_are_impossible_even_library_wide(svc, session):
    """The generated scope_key makes this work; a plain unique index would not,
    because SQLite treats NULL subcollection_id values as distinct."""
    from numis.models import FeatureBinding

    field = svc.create_field("diameter", "Diameter", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=field)
    session.add(
        FeatureBinding(
            feature="labels",
            purpose="cutout_diameter",
            subcollection_id=None,
            target_kind="field",
            field_definition_id=field.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_purpose_may_be_bound_per_subcollection(svc, modern, ancients):
    field = svc.create_field("diameter", "Diameter", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=field)
    svc.set_binding("labels", "cutout_diameter", field=field, subcollection=modern)
    svc.set_binding("labels", "cutout_diameter", field=field, subcollection=ancients)
    assert svc.resolve_binding("labels", "cutout_diameter", subcollection=modern) is not None


def test_the_country_binding_replaces_the_generators_fixed_column(svc, modern):
    """``country_column = B`` in config.txt becomes a question answered once."""
    country = svc.create_field("country", "Country", "text")
    svc.set_binding("labels", "flag_country", field=country)
    coin = svc.add_specimen(modern, values={"country": "AT"})
    assert svc.bound_value(coin, "labels", "flag_country") == "AT"


def test_a_bound_field_with_no_value_returns_none_rather_than_failing(svc, modern):
    diameter = svc.create_field("diameter", "Diameter", "dimension")
    svc.set_binding("labels", "cutout_diameter", field=diameter)
    coin = svc.add_specimen(modern)
    assert svc.bound_value(coin, "labels", "cutout_diameter") is None
