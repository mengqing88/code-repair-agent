from target import redeem


def test_insufficient_balance_clamps_to_zero():
    assert redeem(20, 50) == 0


def test_one_cent_short():
    assert redeem(49, 50) == 0


def test_negative_balance_with_cost():
    assert redeem(-5, 10) == 0
