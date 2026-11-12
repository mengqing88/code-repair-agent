import pytest

from target import amounts_equal


def test_integer_and_its_string_form_are_equal():
    assert amounts_equal(100, "100") is True


def test_string_amount_with_whitespace_is_parsed():
    assert amounts_equal(" 100 ", "100") is True


def test_float_string_with_whitespace_is_parsed():
    assert amounts_equal(" 100.5 ", 100.5) is True
