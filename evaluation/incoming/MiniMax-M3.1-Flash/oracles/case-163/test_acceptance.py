import pytest

from target import month_end


def test_january_has_thirty_one_days():
    assert month_end(2024, 1) == 31


def test_march_has_thirty_one_days():
    assert month_end(2023, 3) == 31


def test_february_in_leap_year_has_twenty_nine_days():
    assert month_end(2024, 2) == 29
