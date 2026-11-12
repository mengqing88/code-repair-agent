from target import sort_records

def test_single_asc():
    rs = [{"n": 2, "v": 1}, {"n": 1, "v": 1}]
    assert [r["n"] for r in sort_records(rs, ("n",))] == [1, 2]

def test_single_desc():
    rs = [{"v": 1}, {"v": 3}, {"v": 2}]
    assert [r["v"] for r in sort_records(rs, ("-v",))] == [3, 2, 1]

def test_empty_keys_identity():
    rs = [{"v": 2}, {"v": 1}]
    assert sort_records(rs, ()) == [{"v": 2}, {"v": 1}]
