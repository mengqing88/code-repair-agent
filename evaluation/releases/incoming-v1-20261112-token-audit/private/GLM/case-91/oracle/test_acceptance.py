import pytest
from target import tiered_discount

def test_exact_1000():
    assert tiered_discount(1000) == 800.0

def test_exact_500():
    assert tiered_discount(500) == 450.0

def test_exact_200():
    assert tiered_discount(200) == 190.0
