from target import Deduper


def test_same_id_different_time_is_duplicate():
    d = Deduper()
    assert d.process("a", "x", 1) is True
    assert d.process("a", "x", 2) is False


def test_same_id_different_payload_is_duplicate():
    d = Deduper()
    assert d.process("b", "x", 1) is True
    assert d.process("b", "y", 7) is False


def test_three_retries_are_all_duplicates():
    d = Deduper()
    assert d.process("c", "x", 1) is True
    assert d.process("c", "x", 2) is False
    assert d.process("c", "x", 3) is False
