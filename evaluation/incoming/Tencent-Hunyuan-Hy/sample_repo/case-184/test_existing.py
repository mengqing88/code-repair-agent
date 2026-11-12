from target import OrderProcessor


def test_first_payment_succeeds():
    p = OrderProcessor()
    assert p.apply_payment("e1", 100) is True
    assert p.state == "paid"
    assert p.total_charged == 100


def test_same_event_id_is_idempotent():
    p = OrderProcessor()
    assert p.apply_payment("e1", 100) is True
    assert p.apply_payment("e1", 100) is False
    assert p.total_charged == 100


def test_cancelled_order_rejects_payment():
    p = OrderProcessor()
    assert p.cancel() is True
    assert p.apply_payment("e1", 100) is False
    assert p.state == "cancelled"
    assert p.total_charged == 0
