
from target import PaymentProcessor


def test_different_request_ids_with_same_amount_are_charged_separately():
    processor = PaymentProcessor()
    first = processor.charge("req-a", 100)
    second = processor.charge("req-b", 100)
    assert first["request_id"] == "req-a"
    assert second["request_id"] == "req-b"
    assert processor.charge_count == 2


def test_same_request_id_with_changed_amount_is_still_idempotent():
    processor = PaymentProcessor()
    processor.charge("req-a", 100)
    again = processor.charge("req-a", 250)
    assert again == {"request_id": "req-a", "amount": 100, "status": "charged"}
    assert processor.charge_count == 1


def test_three_requests_with_the_same_amount_are_three_charges():
    processor = PaymentProcessor()
    processor.charge("req-a", 100)
    processor.charge("req-b", 100)
    processor.charge("req-c", 100)
    assert processor.charge_count == 3
    assert processor.charge("req-c", 100)["request_id"] == "req-c"
