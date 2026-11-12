
import pytest

from target import Order


def test_initial_status_is_pending():
    assert Order().status == "pending"


def test_legal_transition_updates_status():
    order = Order()
    assert order.transition_to("paid") == "paid"
    assert order.status == "paid"


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        Order("unknown")
    with pytest.raises(ValueError):
        Order().transition_to("refunded")
