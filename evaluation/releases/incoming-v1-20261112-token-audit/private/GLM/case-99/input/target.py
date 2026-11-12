def compare_versions(a, b):
    """比较点分版本号：a>b 返回 1，相等返回 0，a<b 返回 -1。
    每段为非负整数；缺省段视为 0。"""
    sa = a.split(".")
    sb = b.split(".")
    if sa > sb:
        return 1
    if sa < sb:
        return -1
    return 0
