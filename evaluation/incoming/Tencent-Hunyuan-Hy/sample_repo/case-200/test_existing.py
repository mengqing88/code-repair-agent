from target import redeem


def test_sufficient_balance():
    assert redeem(100, 30) == 70


def test_exact_balance():
    assert redeem(50, 50) == 0


def test_negative_balance_with_zero_cost():
    assert redeem(-10, 0) == 0
