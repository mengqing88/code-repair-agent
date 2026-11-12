import pytest
from target import Inventory

def test_failed_withdraw_keeps_state():
    inv = Inventory(5)
    with pytest.raises(ValueError):
        inv.withdraw(6)
    assert inv.stock == 5

def test_valid_withdraw_after_failure():
    inv = Inventory(5)
    with pytest.raises(ValueError):
        inv.withdraw(9)
    assert inv.withdraw(3) == 2

def test_boundary_insufficient():
    inv = Inventory(5)
    with pytest.raises(ValueError):
        inv.withdraw(6)
    assert inv.stock == 5
