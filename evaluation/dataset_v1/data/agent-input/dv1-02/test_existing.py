"""Existing behaviour of the inventory service."""

import pytest

from inventory import Inventory, InventoryError


def test_added_stock_is_available():
    inv = Inventory()
    inv.add("sku-1", 4)
    assert inv.available["sku-1"] == 4


def test_reserving_more_than_available_is_rejected():
    inv = Inventory()
    inv.add("sku-1", 2)
    with pytest.raises(InventoryError):
        inv.reserve("sku-1", 3)


def test_commit_without_reservation_is_rejected():
    inv = Inventory()
    with pytest.raises(InventoryError):
        inv.commit("sku-1", 1)
