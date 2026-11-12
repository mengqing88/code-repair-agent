from target import amounts_equal


def test_identical_integers_are_equal():
    assert amounts_equal(100, 100) is True


def test_same_type_string_values_are_equal():
    assert amounts_equal("100", "100") is True


def test_unparsable_string_is_not_equal_to_number():
    assert amounts_equal("abc", 100) is False
