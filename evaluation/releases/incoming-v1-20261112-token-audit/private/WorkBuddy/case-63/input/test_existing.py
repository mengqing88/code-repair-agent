import pytest
from target import buy_x_get_y_free

def test_all_equal():
    assert buy_x_get_y_free([10, 10, 10], 2, 1) == 20

def test_no_group():
    assert buy_x_get_y_free([5, 5], 2, 1) == 10

def test_empty():
    assert buy_x_get_y_free([], 2, 1) == 0
