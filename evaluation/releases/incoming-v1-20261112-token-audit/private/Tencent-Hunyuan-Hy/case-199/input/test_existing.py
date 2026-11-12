import pytest

from target import page_count


def test_exact_multiple():
    assert page_count(10, 5) == 2


def test_no_items():
    assert page_count(0, 5) == 0


def test_invalid_page_size():
    with pytest.raises(ValueError):
        page_count(10, 0)
