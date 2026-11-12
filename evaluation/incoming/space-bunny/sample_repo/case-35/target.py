
"""Transaction sorting."""


def sort_transactions(transactions):
    """Return transactions sorted by amount desc, timestamp asc, id asc."""
    return sorted(transactions, key=lambda t: (-t["amount"], t["timestamp"], t["id"]))
