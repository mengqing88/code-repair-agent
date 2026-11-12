import pytest
from target import billing_seconds

def test_exact():
    assert billing_seconds(1000) == 1

def test_double():
    assert billing_seconds(2000) == 2

def test_zero():
    assert billing_seconds(0) == 1
