"""Transfer orchestration."""

from __future__ import annotations

from .account import LedgerError


def transfer(accounts, journal, donor, payee, amount):
    """Move ``amount`` from ``donor`` to ``payee``."""
    if amount <= 0:
        raise LedgerError("amount must be positive")
    mark = journal.append({"donor": donor, "payee": payee, "amount": amount})
    accounts[donor].withdraw(amount)
    accounts[payee].deposit(amount)
    return mark
