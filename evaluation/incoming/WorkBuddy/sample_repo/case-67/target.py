def is_event_live(start, end, now_local, tz_hours):
    now_utc = now_local + tz_hours * 3600
    return start <= now_utc <= end
