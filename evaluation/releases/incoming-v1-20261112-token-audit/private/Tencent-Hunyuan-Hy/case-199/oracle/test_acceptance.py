from target import page_count


def test_remainder_needs_extra_page():
    assert page_count(11, 5) == 3


def test_less_than_one_page():
    assert page_count(3, 10) == 1


def test_remainder_of_one():
    assert page_count(6, 5) == 2
