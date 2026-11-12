import pytest

from target import merge_config


def test_list_elements_are_merged_by_index():
    result = merge_config(
        {"routes": [{"path": "/a", "timeout": 10}, {"path": "/b", "timeout": 20}]},
        {"routes": [{"timeout": 99}]},
    )
    assert result == {"routes": [{"path": "/a", "timeout": 99}, {"path": "/b", "timeout": 20}]}


def test_override_item_is_merged_into_the_base_item_at_the_same_index():
    result = merge_config(
        {"routes": [{"path": "/a", "timeout": 10}]},
        {"routes": [{"timeout": 99}, {"path": "/c"}]},
    )
    assert result == {"routes": [{"path": "/a", "timeout": 99}, {"path": "/c"}]}


def test_longer_base_list_is_preserved():
    result = merge_config({"routes": [{"path": "/a"}, {"path": "/b"}]}, {"routes": []})
    assert result == {"routes": [{"path": "/a"}, {"path": "/b"}]}
