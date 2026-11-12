from target import OrderProcessor


def test_new_event_id_after_paid_is_rejected():
    p = OrderProcessor()
    assert p.apply_payment("e1", 100) is True
    assert p.apply_payment("e2", 50) is False
    assert p.total_charged == 100
    assert p.state == "paid"


def test_repeated_retries_after_paid_never_accumulate():
    p = OrderProcessor()
    assert p.apply_payment("e1", 100) is True
    assert p.apply_payment("e2", 50) is False
    assert p.apply_payment("e3", 70) is False
    assert p.total_charged == 100


def test_cancel_after_paid_then_new_payment_still_rejected():
    p = OrderProcessor()
    assert p.apply_payment("e1", 100) is True
    assert p.cancel() is False
    assert p.state == "paid"
    assert p.apply_payment("e2", 70) is False
    assert p.total_charged == 100
