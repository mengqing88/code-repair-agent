from target import payable_cents


def test_full_price_reaches_threshold():
    assert payable_cents(12000, 100, 10000, 1500) == 10500


def test_discounted_still_far_above_threshold():
    assert payable_cents(20000, 90, 10000, 1500) == 16500


def test_below_threshold_no_reduction():
    assert payable_cents(8000, 100, 10000, 1500) == 8000
