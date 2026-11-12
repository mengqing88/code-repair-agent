from target import merge_settings

def test_nested_sibling_preserved():
    d = {"db": {"host": "a", "port": 1}}
    r = merge_settings(d, {"db": {"port": 2}})
    assert r == {"db": {"host": "a", "port": 2}}

def test_nested_scalar_update():
    d = {"db": {"host": "a", "port": 1}}
    r = merge_settings(d, {"db": {"host": "b"}})
    assert r == {"db": {"host": "b", "port": 1}}

def test_deep_two_levels():
    d = {"srv": {"db": {"host": "h", "port": 1}, "cache": {"on": False}}}
    r = merge_settings(d, {"srv": {"db": {"port": 9}}})
    assert r == {"srv": {"db": {"host": "h", "port": 9}, "cache": {"on": False}}}
