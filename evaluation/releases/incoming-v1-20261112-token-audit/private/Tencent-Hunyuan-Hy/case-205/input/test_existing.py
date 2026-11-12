from target import last_index_of


def test_target_before_last_position():
    assert last_index_of([1, 2, 1, 3], 1) == 2


def test_target_absent():
    assert last_index_of([1, 2, 3], 9) == -1


def test_empty_items():
    assert last_index_of([], 1) == -1
