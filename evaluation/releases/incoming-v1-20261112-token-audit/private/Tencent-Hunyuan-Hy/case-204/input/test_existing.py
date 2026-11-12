from target import Deduper


def test_distinct_ids_are_independent():
    d = Deduper()
    assert d.process("a", "x", 1) is True
    assert d.process("b", "x", 2) is True


def test_identical_call_is_duplicate():
    d = Deduper()
    assert d.process("a", "x", 1) is True
    assert d.process("a", "x", 1) is False


def test_first_call_always_accepted():
    d = Deduper()
    assert d.process("z", "p", 9) is True
