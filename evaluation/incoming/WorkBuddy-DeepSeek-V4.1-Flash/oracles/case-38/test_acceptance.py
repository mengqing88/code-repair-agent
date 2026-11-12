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


def test_acceptance_1():
    assert _cancel_stays_cancelled() == 'cancelled'


def test_acceptance_2():
    assert _cancel_twice_state() == 'cancelled'


def test_acceptance_3():
    assert _cancel_idempotent_returns() == ['cancelled', 'cancelled']
