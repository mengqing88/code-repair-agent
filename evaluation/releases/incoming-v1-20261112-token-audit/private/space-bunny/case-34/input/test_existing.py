
from target import PaymentProcessor


def test_single_charge_is_recorded():
    processor = PaymentProcessor()
    result = processor.charge("req-1", 100)
    assert result == {"request_id": "req-1", "amount": 100, "status": "charged"}
    assert processor.charge_count == 1


def test_repeated_request_id_is_idempotent():
    processor = PaymentProcessor()
    first = processor.charge("req-1", 100)
    second = processor.charge("req-1", 100)
    assert second == first
    assert processor.charge_count == 1


def test_returned_dict_is_a_copy():
    processor = PaymentProcessor()
    result = processor.charge("req-1", 100)
    result["status"] = "tampered"
    assert processor.charge("req-1", 100)["status"] == "charged"
