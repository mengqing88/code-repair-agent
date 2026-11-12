
import pytest

from target import InvalidTransitionError, Order


def test_cannot_skip_pending_to_shipped():
    order = Order()
    with pytest.raises(InvalidTransitionError):
        order.transition_to("shipped")
    assert order.status == "pending"


def test_cancelled_order_cannot_be_reactivated():
    order = Order()
    order.transition_to("cancelled")
    with pytest.raises(InvalidTransitionError):
        order.transition_to("paid")
    assert order.status == "cancelled"


def test_shipped_order_is_final():
    order = Order()
    order.transition_to("paid")
    order.transition_to("shipped")
    with pytest.raises(InvalidTransitionError):
        order.transition_to("cancelled")
    assert order.status == "shipped"
