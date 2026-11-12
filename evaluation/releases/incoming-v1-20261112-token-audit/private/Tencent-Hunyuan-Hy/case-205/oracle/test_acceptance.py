from target import last_index_of


def test_target_only_at_last_position():
    assert last_index_of([1, 2, 3], 3) == 2


def test_target_at_two_last_positions():
    assert last_index_of([4, 5, 5], 5) == 2


def test_single_element_list():
    assert last_index_of([7], 7) == 0
