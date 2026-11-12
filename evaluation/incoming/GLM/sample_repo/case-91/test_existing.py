from target import tiered_discount

def test_high_tier():
    assert tiered_discount(1200) == 960.0

def test_below_all_tiers():
    assert tiered_discount(100) == 100.0

def test_middle_tier():
    assert tiered_discount(700) == 630.0
