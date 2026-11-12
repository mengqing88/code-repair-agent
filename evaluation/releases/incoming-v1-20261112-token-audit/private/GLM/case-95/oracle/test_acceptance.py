from target import sort_records

RS = [
    {"dept": "b", "salary": 30},
    {"dept": "a", "salary": 10},
    {"dept": "a", "salary": 50},
    {"dept": "b", "salary": 5},
]

def test_dept_then_salary_desc():
    out = sort_records(RS, ("dept", "-salary"))
    assert [(r["dept"], r["salary"]) for r in out] == [
        ("a", 50), ("a", 10), ("b", 30), ("b", 5)]

def test_dept_then_name_asc():
    rs = [{"dept": "a", "name": "m"}, {"dept": "b", "name": "a"}, {"dept": "a", "name": "z"}]
    out = sort_records(rs, ("dept", "name"))
    assert [(r["dept"], r["name"]) for r in out] == [("a", "m"), ("a", "z"), ("b", "a")]

def test_three_keys():
    rs = [
        {"c": 1, "age": 30, "name": "b"},
        {"c": 1, "age": 20, "name": "a"},
        {"c": 1, "age": 30, "name": "a"},
    ]
    out = sort_records(rs, ("c", "-age", "name"))
    assert [(r["age"], r["name"]) for r in out] == [(30, "a"), (30, "b"), (20, "a")]
