"""Existing behaviour of the bulk delete."""

import pytest

from bulk import StoreError, Table


def test_a_single_key_is_deleted():
    table = Table()
    table.add("a", 1)
    assert table.delete_many(["a"]) == {"a": 1}
    assert table.snapshot() == {}


def test_an_empty_batch_changes_nothing():
    table = Table()
    table.add("a", 1)
    assert table.delete_many([]) == {}
    assert table.snapshot() == {"a": 1}


def test_a_missing_key_is_reported():
    table = Table()
    table.add("a", 1)
    with pytest.raises(StoreError):
        table.delete_many(["nope"])
