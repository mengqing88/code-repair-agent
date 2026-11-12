from target import tiered_rate


def test_amount_below_first_threshold_has_no_rate():
    assert tiered_rate(50, [(100, 0.1), (200, 0.2)]) == 0.0


def test_amount_between_thresholds_uses_lower_tier():
    assert tiered_rate(150, [(100, 0.1), (200, 0.2)]) == 0.1


def test_amount_above_all_thresholds_uses_top_tier():
    assert tiered_rate(250, [(100, 0.1), (200, 0.2)]) == 0.2
