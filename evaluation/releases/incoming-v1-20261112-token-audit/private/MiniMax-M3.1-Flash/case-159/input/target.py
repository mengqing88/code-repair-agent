"""多来源配置合并。"""


def merge_sources(sources):
    """按优先级从高到低合并多个配置来源。sources 靠前的优先级更高。"""
    merged = {}
    for source in sources:
        for key, value in source.items():
            if key not in merged:
                merged[key] = value
    return merged
