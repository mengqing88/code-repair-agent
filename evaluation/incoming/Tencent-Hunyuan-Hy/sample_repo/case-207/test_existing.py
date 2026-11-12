from target import final_price


def test_no_discount_rate():
    assert final_price(10000, 100, 1500) == 8500


def test_zero_coupon():
    assert final_price(10000, 80, 0) == 8000


def test_coupon_exceeds_price():
    assert final_price(1000, 100, 2000) == 0
