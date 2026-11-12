import pytest

from target import dedupe_keep_order


def test_first_occurrence_order_is_preserved():
    assert dedupe_keep_order(["c", "a", "b"]) == ["c", "a", "b"]


def test_duplicates_keep_their_first_position():
    assert dedupe_keep_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_order_is_preserved_with_many_items():
    assert dedupe_keep_order(["z", "y", "x", "y"]) == ["z", "y", "x"]
