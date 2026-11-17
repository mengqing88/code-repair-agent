"""Stock reservation for an order service."""

from __future__ import annotations


class InventoryError(ValueError):
    """Raised when a stock operation is not allowed."""


class Inventory:
    def __init__(self):
        self.available = {}
        self.reserved = {}

    def add(self, sku, qty):
        if qty <= 0:
            raise InventoryError("qty must be positive")
        self.available[sku] = self.available.get(sku, 0) + qty

    def reserve(self, sku, qty):
        if qty <= 0:
            raise InventoryError("qty must be positive")
        if self.available.get(sku, 0) <= qty:
            raise InventoryError("insufficient stock")
        self.available[sku] -= qty
        self.reserved[sku] = self.reserved.get(sku, 0) + qty

    def commit(self, sku, qty):
        if self.reserved.get(sku, 0) < qty:
            raise InventoryError("nothing reserved")
        self.reserved[sku] -= qty
