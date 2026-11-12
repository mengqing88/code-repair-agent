import pytest
from target import billing_seconds

def test_one_half():
    assert billing_seconds(1500) == 2

def test_one_more():
    assert billing_seconds(1001) == 2

def test_two_plus():
    assert billing_seconds(2001) == 3
