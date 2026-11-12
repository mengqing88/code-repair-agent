from target import merge_sources


def test_single_source_is_copied():
    assert merge_sources([{"a": 1}]) == {"a": 1}


def test_empty_source_list_gives_empty_dict():
    assert merge_sources([]) == {}


def test_key_from_later_source_is_added():
    assert merge_sources([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}
