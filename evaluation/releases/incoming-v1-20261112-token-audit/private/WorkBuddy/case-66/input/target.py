import datetime

def is_subscription_active(start, end, today):
    return start <= today < end
