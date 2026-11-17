"""Existing behaviour of the storage sandbox."""

from storage import Storage


def test_a_plain_name_is_resolved(tmp_path):
    storage = Storage(str(tmp_path))
    assert storage.resolve("data.txt").endswith("data.txt")


def test_a_nested_name_is_resolved(tmp_path):
    storage = Storage(str(tmp_path))
    assert storage.resolve("sub/data.txt").replace("\\", "/").endswith("sub/data.txt")


def test_the_root_itself_is_resolved(tmp_path):
    storage = Storage(str(tmp_path))
    assert storage.resolve("").rstrip("\\/") == str(tmp_path)
