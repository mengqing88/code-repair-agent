import pytest

from target import merge_sources


def test_none_value_does_not_shadow_lower_priority_source():
    assert merge_sources([{"db": None}, {"db": "real"}]) == {"db": "real"}


def test_none_value_key_is_absent_from_result():
    assert merge_sources([{"db": None}]) == {}


def test_none_values_are_skipped_while_other_keys_are_kept():
    assert merge_sources([{"a": None, "x": 1}, {"b": None}]) == {"x": 1}
