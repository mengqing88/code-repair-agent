"""Existing behaviour of the currency helper."""

from decimal import Decimal

from money import to_cents


def test_two_decimals_are_converted():
    assert to_cents("1.23") == 123


def test_zero_stays_zero():
    assert to_cents("0") == 0


def test_negative_amounts_keep_their_sign():
    assert to_cents("-1.50") == -150
