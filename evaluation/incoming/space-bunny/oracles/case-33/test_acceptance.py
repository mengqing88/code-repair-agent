
from target import merge_configs


def test_nested_dicts_are_merged_recursively():
    result = merge_configs(
        {"server": {"host": "localhost", "port": 80}},
        {"server": {"port": 8080}},
    )
    assert result == {"server": {"host": "localhost", "port": 8080}}


def test_three_level_nested_dicts_are_merged_recursively():
    result = merge_configs(
        {"a": {"b": {"c": 1, "d": 2}}},
        {"a": {"b": {"c": 9}}},
    )
    assert result == {"a": {"b": {"c": 9, "d": 2}}}


def test_empty_override_dict_keeps_base_subtree():
    base = {"a": {"b": {"c": 1}}}
    assert merge_configs(base, {"a": {}}) == {"a": {"b": {"c": 1}}}
