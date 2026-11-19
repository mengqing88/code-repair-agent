"""Existing behaviour of the ledger."""

import pytest

from ledger import Account, Journal, LedgerError, transfer


def test_a_transfer_moves_money_and_records_one_entry():
    accounts = {"a": Account(100), "b": Account(0)}
    journal = Journal()
    transfer(accounts, journal, "a", "b", 40)
    assert accounts["a"].balance == 60
    assert accounts["b"].balance == 40
    assert len(journal) == 1


def test_a_non_positive_amount_is_rejected():
    accounts = {"a": Account(100), "b": Account(0)}
    journal = Journal()
    with pytest.raises(LedgerError):
        transfer(accounts, journal, "a", "b", 0)
    assert len(journal) == 0


def test_entries_returns_copies():
    journal = Journal()
    journal.append({"donor": "a", "payee": "b", "amount": 1})
    view = journal.entries()
    view[0]["amount"] = 999
    assert journal.entries()[0]["amount"] == 1
