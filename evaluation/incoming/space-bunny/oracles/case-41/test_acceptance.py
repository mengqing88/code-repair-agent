
from target import parse_row


def test_comma_inside_quoted_field_is_kept():
    assert parse_row('a,"b,c",d') == ["a", "b,c", "d"]


def test_escaped_double_quote_inside_quoted_field():
    assert parse_row('a,"say ""hi""",d') == ["a", 'say "hi"', "d"]


def test_unterminated_quote_raises_value_error():
    import pytest

    with pytest.raises(ValueError):
        parse_row('a,"b,c')
