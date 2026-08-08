"""The append-only ledger, and money derived from it."""

from __future__ import annotations

from datetime import date

from numis.models import SpecimenEvent


def test_fees_add_to_a_purchase_and_come_off_a_sale(svc, modern):
    """The bug found while writing the specification.

    Summing fees in both directions overstated the profit on this exact example as 18875
    when it is really 12675.
    """
    coin = svc.add_specimen(modern)
    bought = svc.add_event(
        coin, "acquired", occurred_on=date(2021, 3, 4),
        amount="125.00", fees="18.75", shipping="8.50",
    )
    sold = svc.add_event(
        coin, "sold", occurred_on=date(2025, 9, 19), amount="310.00", fees="31.00"
    )

    assert bought.net_minor == 15225  # 12500 + 1875 + 850
    assert sold.net_minor == 27900  # 31000 - 3100
    assert svc.realised_profit(coin) == 12675
    assert svc.realised_profit(coin) != 18875


def test_cost_and_proceeds_are_derived_not_stored(svc, modern):
    coin = svc.add_specimen(modern)
    svc.add_event(coin, "acquired", amount="100.00", fees="10.00")
    assert svc.cost_basis(coin) == 11000
    assert svc.proceeds(coin) is None
    assert svc.realised_profit(coin) is None  # still held

    svc.add_event(coin, "sold", amount="150.00", fees="15.00")
    assert svc.proceeds(coin) == 13500
    assert svc.realised_profit(coin) == 2500


def test_events_with_no_money_are_harmless(svc, modern):
    coin = svc.add_specimen(modern)
    event = svc.add_event(coin, "gifted_out", occurred_on=date(2024, 1, 1))
    assert event.net_minor == 0


def test_a_valuation_records_an_estimate_without_touching_cost(svc, modern):
    coin = svc.add_specimen(modern)
    svc.add_event(coin, "acquired", amount="100.00")
    svc.add_event(coin, "valued", amount="400.00")
    assert svc.cost_basis(coin) == 10000
    assert svc.proceeds(coin) is None


def test_correcting_a_mistake_voids_rather_than_edits(svc, modern):
    """The ledger is append-only, so the correction stays visible as a correction."""
    coin = svc.add_specimen(modern)
    wrong = svc.add_event(coin, "acquired", amount="1250.00")
    assert svc.cost_basis(coin) == 125000

    right = svc.add_event(coin, "acquired", amount="125.00")
    svc.void_event(wrong, "typed an extra zero", replacement=right)

    assert wrong.is_void == 1
    assert wrong.void_reason == "typed an extra zero"
    assert right.corrects_event_id == wrong.id
    assert svc.cost_basis(coin) == 12500  # the void entry no longer counts


def test_voided_events_remain_visible_in_the_full_history(svc, modern):
    coin = svc.add_specimen(modern)
    event = svc.add_event(coin, "acquired", amount="10.00")
    svc.void_event(event, "wrong coin")
    assert svc.events_for(coin) == []
    assert len(svc.events_for(coin, include_void=True)) == 1


def test_events_have_no_updated_at_because_nothing_is_updated(svc):
    assert not hasattr(SpecimenEvent, "updated_at")
    assert hasattr(SpecimenEvent, "created_at")


class TestStatusProjection:
    def test_status_follows_the_ledger(self, svc, modern):
        coin = svc.add_specimen(modern)
        assert coin.status == "owned"

        svc.add_event(coin, "acquired", occurred_on=date(2020, 1, 1), amount="10.00")
        assert coin.status == "owned"

        svc.add_event(coin, "sold", occurred_on=date(2024, 1, 1), amount="20.00")
        assert coin.status == "sold"

    def test_voiding_a_sale_returns_the_coin_to_owned(self, svc, modern):
        coin = svc.add_specimen(modern)
        svc.add_event(coin, "acquired", occurred_on=date(2020, 1, 1))
        sale = svc.add_event(coin, "sold", occurred_on=date(2024, 1, 1), amount="20.00")
        assert coin.status == "sold"

        svc.void_event(sale, "sale fell through")
        assert coin.status == "owned"

    def test_sold_coins_stay_in_the_database(self, svc, modern):
        """A collection modelled over time, not just as a snapshot."""
        svc.add_specimen(modern, display_name="kept")
        gone = svc.add_specimen(modern, display_name="gone")
        svc.add_event(gone, "sold", amount="20.00")

        everything = list(svc.session.scalars(svc.live_specimens(modern)))
        assert {c.display_name for c in everything} == {"kept", "gone"}

        owned = [c for c in everything if c.status == "owned"]
        assert [c.display_name for c in owned] == ["kept"]


def test_amounts_accept_typed_text_and_integers(svc, modern):
    coin = svc.add_specimen(modern)
    typed = svc.add_event(coin, "acquired", amount="$1,234.56")
    exact = svc.add_event(coin, "valued", amount=123456)
    assert typed.net_minor == exact.net_minor == 123456
