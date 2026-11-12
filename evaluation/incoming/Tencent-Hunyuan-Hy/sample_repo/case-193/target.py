# 三源配置解析：defaults < env < user（右侧优先级更高）。


def resolve(defaults, env, user):
    """按优先级合并三份配置，返回新字典。"""
    return {**defaults, **user, **env}
