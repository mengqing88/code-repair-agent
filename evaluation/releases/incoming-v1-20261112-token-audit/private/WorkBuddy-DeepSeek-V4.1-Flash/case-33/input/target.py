def format_duration(total_seconds):
    if total_seconds < 0:
        raise ValueError('total_seconds must be non-negative')
    minutes = total_seconds // 60
    return '%d:00' % minutes
