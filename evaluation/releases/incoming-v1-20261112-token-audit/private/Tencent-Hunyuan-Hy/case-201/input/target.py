# 权限校验：必须同时具备 required 中的所有权限。


def has_access(user_perms, required):
    """返回是否允许访问。"""
    return any(p in user_perms for p in required)
