import pytest

from target import append_records


def test_empty_new_record_raises():
    with pytest.raises(ValueError):
        append_records([], {})


def test_new_record_is_appended():
    assert append_records([], {"id": 1}) == [{"id": 1}]


def test_existing_records_are_kept():
    assert append_records([{"id": 1}], {"id": 2}) == [{"id": 1}, {"id": 2}]
