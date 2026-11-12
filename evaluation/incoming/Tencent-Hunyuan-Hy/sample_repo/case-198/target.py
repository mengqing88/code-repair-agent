# 访问路径去重：去除重复项并保留首次出现顺序。


def unique_paths(paths):
    """返回去重后的路径列表。"""
    return [p for p in sorted(set(paths))]
