from target import group_by_team

def test_case_sensitive_keys():
    ms = [{"name": "a", "team": "sales"}, {"name": "b", "team": "Sales"}]
    r = group_by_team(ms)
    assert r == {"sales": ["a"], "Sales": ["b"]}

def test_original_key_not_uppercased():
    ms = [{"name": "a", "team": "hr"}]
    assert "hr" in group_by_team(ms)

def test_mixed_case_three_groups():
    ms = [
        {"name": "a", "team": "ops"},
        {"name": "b", "team": "OPS"},
        {"name": "c", "team": "Ops"},
    ]
    r = group_by_team(ms)
    assert sorted(r.keys()) == ["OPS", "Ops", "ops"]
