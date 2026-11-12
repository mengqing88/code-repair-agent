import pytest

from target import allocate_refund


def test_empty_weights_return_empty_list():
    assert allocate_refund(100, []) == []


def test_zero_total_returns_all_zeros():
    assert allocate_refund(0, [1, 2, 3]) == [0, 0, 0]


def test_non_positive_weight_sum_raises():
    with pytest.raises(ValueError):
        allocate_refund(100, [0, 0])
