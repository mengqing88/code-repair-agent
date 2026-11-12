def group_by_team(members):
    """按 team 分组：键为 team 原始字符串（大小写敏感），
    值为按输入顺序排列的成员名列表。"""
    groups = {}
    for m in members:
        key = m["team"].upper()
        groups.setdefault(key, []).append(m["name"])
    return groups
