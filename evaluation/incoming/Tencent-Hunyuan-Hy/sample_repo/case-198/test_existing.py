from target import unique_paths


def test_already_sorted_without_duplicates():
    assert unique_paths(["a", "b", "c"]) == ["a", "b", "c"]


def test_empty_paths():
    assert unique_paths([]) == []


def test_single_path():
    assert unique_paths(["x"]) == ["x"]
