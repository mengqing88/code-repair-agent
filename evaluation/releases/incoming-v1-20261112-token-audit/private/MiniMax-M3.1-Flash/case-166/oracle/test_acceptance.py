import pytest

from target import cache_key


def test_different_part_sequences_do_not_collide():
    assert cache_key("ab", "c") != cache_key("a", "bc")


def test_ambiguous_split_of_digits_is_resolved():
    assert cache_key(12, 3) != cache_key(1, 23)


def test_key_depends_on_part_count():
    assert cache_key("a", "b") != cache_key("ab")
