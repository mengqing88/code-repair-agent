from target import dedupe_keep_order


def test_empty_list_returns_empty_list():
    assert dedupe_keep_order([]) == []


def test_single_element_is_kept():
    assert dedupe_keep_order(["a"]) == ["a"]


def test_all_distinct_elements_are_kept():
    assert sorted(dedupe_keep_order(["a", "b", "c"])) == ["a", "b", "c"]
