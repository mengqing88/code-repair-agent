from target import needs_restock


def test_exactly_at_threshold():
    assert needs_restock(10, 10) is True


def test_zero_threshold_and_zero_stock():
    assert needs_restock(0, 0) is True


def test_several_equal_pairs():
    for value in (1, 5, 100):
        assert needs_restock(value, value) is True
