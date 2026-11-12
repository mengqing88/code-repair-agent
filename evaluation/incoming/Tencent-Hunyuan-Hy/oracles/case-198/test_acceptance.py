from target import unique_paths


def test_first_occurrence_order_is_not_lexicographic():
    assert unique_paths(["c", "a", "b"]) == ["c", "a", "b"]


def test_duplicates_keep_first_position():
    assert unique_paths(["b", "a", "b", "c"]) == ["b", "a", "c"]


def test_numeric_strings_keep_input_order():
    assert unique_paths(["10", "2", "10", "1"]) == ["10", "2", "1"]
