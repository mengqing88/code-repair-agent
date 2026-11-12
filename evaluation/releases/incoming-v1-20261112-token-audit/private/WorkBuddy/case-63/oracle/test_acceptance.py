import pytest
from target import buy_x_get_y_free

def test_cheapest_freed():
    assert buy_x_get_y_free([10, 10, 5], 2, 1) == 20

def test_two_groups():
    assert buy_x_get_y_free([100, 50, 50, 30, 30, 30], 2, 1) == 210

def test_partial_group():
    assert buy_x_get_y_free([30, 20, 20, 10, 10], 2, 1) == 70
