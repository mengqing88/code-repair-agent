# 默认配置与用户覆盖配置的合并。返回值一律为新对象，不修改入参。


def merge_config(defaults, overrides):
    """递归合并两个配置字典，返回新字典。"""
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        raise TypeError("both arguments must be dict")

    result = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            # 错误：整体替换嵌套字典，导致未指定的默认子键丢失。
            result[key] = dict(value)
        else:
            result[key] = value
    return result
