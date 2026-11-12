
from target import merge_configs


def test_flat_override_wins():
    assert merge_configs({"mode": "safe", "port": 80}, {"port": 8080}) == {"mode": "safe", "port": 8080}


def test_non_dict_values_are_replaced_not_merged():
    result = merge_configs({"tags": ["a", "b"]}, {"tags": ["c"]})
    assert result == {"tags": ["c"]}


def test_inputs_are_not_mutated():
    base = {"mode": "safe"}
    override = {"port": 8080}
    merge_configs(base, override)
    assert base == {"mode": "safe"}
    assert override == {"port": 8080}
