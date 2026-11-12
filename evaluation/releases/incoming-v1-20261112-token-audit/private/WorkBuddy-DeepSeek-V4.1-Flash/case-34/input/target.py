from datetime import date, timedelta

def needs_renewal(expires_on, today):
    return expires_on < today + timedelta(days=1)
