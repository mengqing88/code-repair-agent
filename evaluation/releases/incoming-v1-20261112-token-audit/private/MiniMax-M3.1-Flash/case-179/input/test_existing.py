import pytest

from target import merge_config


def test_none_override_keeps_base_value():
    assert merge_config({"a": 1}, {"a": None}) == {"a": 1}


def test_scalar_override_wins():
    assert merge_config({"a": 1}, {"a": 2}) == {"a": 2}


def test_nested_dict_is_merged():
    assert merge_config({"a": {"x": 1, "y": 2}}, {"a": {"x": 9}}) == {"a": {"x": 9, "y": 2}}
