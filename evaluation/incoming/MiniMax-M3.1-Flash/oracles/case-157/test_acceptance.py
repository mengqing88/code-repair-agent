import pytest

from target import allocate_refund


def test_shares_sum_back_to_total():
    assert sum(allocate_refund(100, [1, 1, 1])) == 100


def test_uneven_split_is_evenly_rebalanced():
    assert allocate_refund(100, [1, 1, 1]) == [34, 33, 33]


def test_remainder_is_distributed_to_the_first_entries():
    assert allocate_refund(101, [1, 1, 1]) == [34, 34, 33]
