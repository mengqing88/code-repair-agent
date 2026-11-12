from target import amounts_match


def test_exactly_equal():
    assert amounts_match(10.0, 10.0) is True


def test_clearly_different():
    assert amounts_match(10.0, 11.0) is False


def test_small_but_significant_difference():
    assert amounts_match(1.0, 1.001) is False
