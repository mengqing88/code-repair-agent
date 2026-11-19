"""Existing behaviour of the invoice total."""

from decimal import Decimal

from money_total import total


def test_two_plain_amounts_add_up():
    assert total(["1.50", "2.50"]) == Decimal("4.00")


def test_negative_amounts_are_allowed():
    assert total(["-1", "1"]) == Decimal("0")


def test_a_single_amount_is_returned():
    assert total(["7.25"]) == Decimal("7.25")
