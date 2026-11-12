from target import lowest_price


def test_all_positive_prices():
    assert lowest_price([10, 20, 5]) == 5


def test_single_positive_price():
    assert lowest_price([7]) == 7


def test_minimum_at_the_end():
    assert lowest_price([9, 8, 7]) == 7
