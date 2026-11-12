import pytest

from target import cache_key


def test_single_part_is_used_verbatim():
    assert cache_key("user:1") == "user:1"


def test_empty_parts_give_empty_key():
    assert cache_key() == ""


def test_invalid_part_type_raises():
    with pytest.raises(TypeError):
        cache_key("a", 1.5)
