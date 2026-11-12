from target import dedup_keep_order

def test_first_occurrence_order():
    assert dedup_keep_order([5, 3, 5, 1, 3]) == [5, 3, 1]

def test_ints_order():
    assert dedup_keep_order([9, 7, 9, 8]) == [9, 7, 8]

def test_strings_order():
    assert dedup_keep_order([10, 33, 10, 5, 33]) == [10, 33, 5]
