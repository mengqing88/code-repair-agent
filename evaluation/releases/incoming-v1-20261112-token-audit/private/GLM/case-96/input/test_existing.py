from target import group_by_team

def test_uniform_case():
    ms = [{"name": "a", "team": "DEV"}, {"name": "b", "team": "DEV"}]
    assert group_by_team(ms) == {"DEV": ["a", "b"]}

def test_single_member():
    ms = [{"name": "a", "team": "OPS"}]
    assert group_by_team(ms) == {"OPS": ["a"]}

def test_value_order_preserved():
    ms = [{"name": "b", "team": "DEV"}, {"name": "a", "team": "DEV"}]
    assert group_by_team(ms)["DEV"] == ["b", "a"]
