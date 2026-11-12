from target import final_price


def test_discount_then_coupon():
    assert final_price(10000, 50, 1500) == 3500


def test_non_round_discount():
    assert final_price(9000, 90, 1000) == 7100


def test_coupon_larger_than_discounted_amount():
    assert final_price(10000, 50, 6000) == 0
