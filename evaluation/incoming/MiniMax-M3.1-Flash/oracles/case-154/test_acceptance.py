import pytest

from target import merge_config


def test_nested_override_keeps_other_nested_keys():
    result = merge_config({"svc": {"timeout": 10, "retries": 2}}, {"svc": {"timeout": 30}})
    assert result == {"svc": {"timeout": 30, "retries": 2}}


def test_two_level_nested_override_is_merged():
    result = merge_config({"db": {"pool": {"size": 5, "max": 10}}}, {"db": {"pool": {"size": 20}}})
    assert result == {"db": {"pool": {"size": 20, "max": 10}}}


def test_partial_nested_override_keeps_sibling_keys():
    result = merge_config({"cache": {"ttl": 60, "size": 100, "mode": "lru"}}, {"cache": {"ttl": 120}})
    assert result == {"cache": {"ttl": 120, "size": 100, "mode": "lru"}}
