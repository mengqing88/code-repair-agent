import pytest

from target import append_records


def test_appended_record_does_not_share_nested_dict():
    original = {"id": 1, "meta": {"tag": "x"}}
    result = append_records([], original)
    result[0]["meta"]["tag"] = "changed"
    assert original["meta"]["tag"] == "x"


def test_appended_record_is_not_the_same_object():
    original = {"id": 1, "meta": {"tag": "x"}}
    result = append_records([], original)
    assert result[0] is not original
    assert result[0]["meta"] is not original["meta"]


def test_mutating_the_appended_record_does_not_change_input():
    original = {"id": 1}
    result = append_records([{"id": 0}], original)
    result[-1]["id"] = 99
    assert original["id"] == 1
