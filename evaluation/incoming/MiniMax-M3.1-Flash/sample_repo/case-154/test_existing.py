from target import merge_config


def test_flat_override_wins():
    assert merge_config({"a": 1}, {"a": 2}) == {"a": 2}


def test_key_only_in_defaults_is_preserved():
    assert merge_config({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_override_replaces_dict_with_scalar():
    assert merge_config({"a": {"x": 1}}, {"a": 5}) == {"a": 5}
