import pytest
from target import compute_total

def test_simple_sum():
    assert compute_total([100.0, 200.0], 0.1) == 330.0

def test_zero_tax():
    assert compute_total([50.0], 0.0) == 50.0

def test_empty():
    assert compute_total([], 0.2) == 0.0
