import pytest

from target import next_state


def test_new_order_cannot_be_shipped_directly():
    with pytest.raises(ValueError):
        next_state("NEW", "SHIP")


def test_delivered_order_cannot_be_paid_again():
    with pytest.raises(ValueError):
        next_state("DELIVERED", "PAY")


def test_cancelled_order_cannot_be_cancelled_again():
    with pytest.raises(ValueError):
        next_state("CANCELLED", "CANCEL")
