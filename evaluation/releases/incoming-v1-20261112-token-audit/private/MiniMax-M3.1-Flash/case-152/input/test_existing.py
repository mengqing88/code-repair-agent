from target import round_money


def test_round_to_two_decimals():
    assert round_money(3.14159) == 3.14


def test_round_whole_amount():
    assert round_money(100) == 100.0


def test_round_number_below_half():
    assert round_money(7.777, 2) == 7.78
