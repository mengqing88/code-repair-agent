from target import dedup_keep_order

def test_unique_content():
    r = dedup_keep_order([1, 2, 3])
    assert set(r) == {1, 2, 3} and len(r) == 3

def test_empty():
    assert dedup_keep_order([]) == []

def test_all_same():
    assert dedup_keep_order(["x", "x", "x"]) == ["x"]
