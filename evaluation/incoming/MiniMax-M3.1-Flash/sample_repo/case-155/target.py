"""配置优先级解析。"""


def resolve_setting(key, overrides, defaults):
    """优先使用 overrides 中的值，其次使用 defaults 中的值。"""
    if key in overrides and overrides[key]:
        return overrides[key]
    return defaults.get(key)
