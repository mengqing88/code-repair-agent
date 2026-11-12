from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _cancel_twice_state():
    order = OrderState('new')
    order.transition('cancelled')
    order.transition('cancelled')
    return order.status

def _cancel_idempotent_returns():
    order = OrderState('cancelled')
    return [order.transition('cancelled'), order.transition('cancelled')]

def _cancel_reject_paid():
    order = OrderState('new')
    order.transition('cancelled')
    return _raises(lambda: order.transition('paid'))

def _cancel_stays_cancelled():
    order = OrderState('cancelled')
    order.transition('cancelled')
    return order.status


def test_existing_1():
    assert OrderState().transition('paid') == 'paid'


def test_existing_2():
    assert OrderState('paid').transition('shipped') == 'shipped'


def test_existing_3():
    assert _raises(lambda: OrderState('refunded').transition('paid'))
