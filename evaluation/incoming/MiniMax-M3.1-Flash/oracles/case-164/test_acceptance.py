import pytest

from target import is_leap_year


def test_eighteenth_hundred_is_not_leap():
    assert is_leap_year(1800) is False


def test_nineteenth_hundred_is_not_leap():
    assert is_leap_year(1900) is False


def test_twenty_first_hundred_is_not_leap():
    assert is_leap_year(2100) is False
