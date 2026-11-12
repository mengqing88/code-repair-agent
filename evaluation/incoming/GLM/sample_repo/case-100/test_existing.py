import pytest
from target import Inventory

def test_successful_withdraw():
    inv = Inventory(10)
    assert inv.withdraw(4) == 6

def test_chained_withdrawals():
    inv = Inventory(10)
    inv.withdraw(2)
    inv.withdraw(3)
    assert inv.stock == 5

def test_exact_to_zero():
    inv = Inventory(10)
    assert inv.withdraw(10) == 0
