
import pytest

from target import chunk_records


def test_exact_multiple_produces_full_chunks():
    assert chunk_records([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_empty_input_returns_empty_list():
    assert chunk_records([], 3) == []


def test_non_positive_size_is_rejected():
    with pytest.raises(ValueError):
        chunk_records([1, 2], 0)
    with pytest.raises(ValueError):
        chunk_records([1, 2], -1)
