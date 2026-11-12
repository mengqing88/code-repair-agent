# 限流配置：读取阈值，未配置时使用默认值 100。


def effective_limit(config):
    """返回生效的限流阈值。"""
    return config.get("limit") or 100
