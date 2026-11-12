def merge_settings(defaults, overrides):
    """深度合并配置：overrides 的标量覆盖 defaults；
    同键两边都是 dict 时递归合并，保留未覆盖的兄弟键。返回新 dict。"""
    result = dict(defaults)
    for key, value in overrides.items():
        result[key] = value
    return result
