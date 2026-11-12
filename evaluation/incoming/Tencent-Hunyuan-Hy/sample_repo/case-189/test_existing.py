from target import sum_valid


def test_all_positive():
    assert sum_valid([10, 20, 30]) == 60


def test_empty_orders():
    assert sum_valid([]) == 0


def test_invalid_at_the_end():
    assert sum_valid([10, 20, -1]) == 30
