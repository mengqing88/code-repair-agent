from target import next_state


def test_new_order_can_be_paid():
    assert next_state("NEW", "PAY") == "PAID"


def test_new_order_can_be_cancelled():
    assert next_state("NEW", "CANCEL") == "CANCELLED"


def test_unknown_event_raises():
    try:
        next_state("NEW", "REFUND")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
