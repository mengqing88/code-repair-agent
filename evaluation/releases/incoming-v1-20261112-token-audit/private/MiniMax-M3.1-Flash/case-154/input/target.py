"""配置合并。"""


def merge_config(defaults, overrides):
    """把 overrides 合并进 defaults，返回新的配置字典。"""
    merged = dict(defaults)
    merged.update(overrides)
    return merged
