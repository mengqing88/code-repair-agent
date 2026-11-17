"""Existing behaviour of the atomic writer."""

import os

from filewriter import write_atomic


def test_chunks_are_written_in_order(tmp_path):
    target = write_atomic(str(tmp_path), "out.txt", ["ab", "cd"])
    assert open(target, encoding="utf-8").read() == "abcd"


def test_an_existing_file_is_replaced(tmp_path):
    (tmp_path / "out.txt").write_text("old", encoding="utf-8")
    write_atomic(str(tmp_path), "out.txt", ["new"])
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "new"


def test_the_returned_path_points_at_the_target(tmp_path):
    target = write_atomic(str(tmp_path), "out.txt", ["x"])
    assert os.path.basename(target) == "out.txt"
    assert os.path.exists(target)
