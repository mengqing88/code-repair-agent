"""Account balances."""

from __future__ import annotations


class LedgerError(ValueError):
    """Raised when a ledger operation is not allowed."""


class Account:
    def __init__(self, balance=0, frozen=False):
        if balance < 0:
            raise LedgerError("balance must be >= 0")
        self.balance = balance
        self.frozen = frozen

    def withdraw(self, amount):
        if self.frozen:
            raise LedgerError("account is frozen")
        if amount > self.balance:
            raise LedgerError("insufficient funds")
        self.balance -= amount

    def deposit(self, amount):
        if self.frozen:
            raise LedgerError("account is frozen")
        self.balance += amount
