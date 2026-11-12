from target import payable_cents


def test_reduction_must_use_discounted_amount():
    assert payable_cents(12000, 50, 10000, 1500) == 6000


def test_one_cent_below_threshold_after_discount():
    assert payable_cents(19998, 50, 10000, 1500) == 9999


def test_full_price_exactly_at_threshold_after_large_discount():
    assert payable_cents(10000, 10, 10000, 1500) == 1000
