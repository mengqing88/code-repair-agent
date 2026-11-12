from target import sum_valid


def test_invalid_at_the_beginning():
    assert sum_valid([-1, 10, 20]) == 30


def test_invalid_in_the_middle():
    assert sum_valid([10, -5, 20, 30]) == 60


def test_multiple_invalid_values():
    assert sum_valid([5, 0, 7, -2, 9]) == 21
