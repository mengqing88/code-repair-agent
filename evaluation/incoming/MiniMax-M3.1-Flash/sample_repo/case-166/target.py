"""缓存键构造。"""


def cache_key(*parts):
    """由多个片段构造缓存键。"""
    for part in parts:
        if not isinstance(part, (str, int)):
            raise TypeError("cache key parts must be str or int")
    return "".join(str(part) for part in parts)
