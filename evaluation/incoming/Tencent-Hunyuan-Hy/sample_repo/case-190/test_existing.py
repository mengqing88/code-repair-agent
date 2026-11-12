import pytest

from target import needs_restock


def test_clearly_below_threshold():
    assert needs_restock(3, 10) is True


def test_clearly_above_threshold():
    assert needs_restock(20, 10) is False


def test_negative_stock_raises():
    with pytest.raises(ValueError):
        needs_restock(-1, 10)
