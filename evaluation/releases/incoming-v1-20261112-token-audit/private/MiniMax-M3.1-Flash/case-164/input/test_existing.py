from target import is_leap_year


def test_ordinary_leap_year():
    assert is_leap_year(2024) is True


def test_ordinary_non_leap_year():
    assert is_leap_year(2023) is False


def test_year_not_divisible_by_four():
    assert is_leap_year(2021) is False
