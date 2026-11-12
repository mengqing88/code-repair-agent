def format_duration(total_seconds):
    """格式化为 "H:MM:SS"：小时不补零可超 24，分/秒固定两位。"""
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return "{0}:{1}:{2}".format(hours, minutes, seconds)
