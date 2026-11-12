import pytest

from target import parse_amounts


def test_line_with_currency_symbol_raises_value_error():
    with pytest.raises(ValueError) as info:
        parse_amounts("10\n$20\n")
    assert "$20" in str(info.value)


def test_line_with_unit_suffix_raises_value_error():
    with pytest.raises(ValueError) as info:
        parse_amounts("10\n20元\n")
    assert "20元" in str(info.value)


def test_signed_number_is_rejected():
    with pytest.raises(ValueError) as info:
        parse_amounts("10\n-20\n")
    assert "-20" in str(info.value)
