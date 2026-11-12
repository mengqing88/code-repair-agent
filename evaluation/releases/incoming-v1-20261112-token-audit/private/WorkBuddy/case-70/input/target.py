import math

def billing_seconds(duration_ms):
    return max(1, duration_ms // 1000)
