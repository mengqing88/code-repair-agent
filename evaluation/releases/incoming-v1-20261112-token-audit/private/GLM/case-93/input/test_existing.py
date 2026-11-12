from target import merge_settings

def test_scalar_override():
    assert merge_settings({"a": 1, "b": 2}, {"a": 3}) == {"a": 3, "b": 2}

def test_new_key_added():
    assert merge_settings({"a": 1}, {"c": 5}) == {"a": 1, "c": 5}

def test_inputs_not_mutated():
    d = {"a": 1}
    o = {"b": 2}
    merge_settings(d, o)
    assert d == {"a": 1} and o == {"b": 2}
