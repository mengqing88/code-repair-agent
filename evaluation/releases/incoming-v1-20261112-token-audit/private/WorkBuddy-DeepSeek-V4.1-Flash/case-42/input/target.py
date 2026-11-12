def split_amounts(entries):
    totals = {'income': 0, 'expense': 0, 'refund': 0}
    for kind, amount in entries:
        if kind not in totals:
            raise ValueError('unknown kind')
        if amount >= 0:
            totals['expense'] += amount
        else:
            totals['refund'] += amount
    return totals
