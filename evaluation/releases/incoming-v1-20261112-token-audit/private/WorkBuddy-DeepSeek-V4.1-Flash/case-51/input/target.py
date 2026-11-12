from datetime import date, timedelta

def split_bill_period(start, end):
    if end < start:
        raise ValueError('end before start')
    key = (start.year, start.month)
    days = (end - start).days
    return [(key, days)]
