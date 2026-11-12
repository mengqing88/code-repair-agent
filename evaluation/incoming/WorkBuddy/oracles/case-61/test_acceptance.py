import pytest
from target import compute_total

def test_sub_cent_three():
    assert compute_total([10.004, 10.004, 10.004], 0.0) == 30.01

def test_sub_cent_two():
    assert compute_total([10.006, 10.006], 0.0) == 20.01

def test_sub_cent_more():
    assert compute_total([10.333, 10.333], 0.0) == 20.67
