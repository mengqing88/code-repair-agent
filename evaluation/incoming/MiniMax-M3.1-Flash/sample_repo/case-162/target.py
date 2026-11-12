"""分组取最新记录。"""


def latest_by(rows, key):
    """按 key 分组，返回每组 ts 最大的那条记录，结果按 key 升序。"""
    groups = {}
    for row in rows:
        group_key = row[key]
        if group_key not in groups:
            groups[group_key] = row
        elif row["ts"] < groups[group_key]["ts"]:
            groups[group_key] = row
    return [groups[group_key] for group_key in sorted(groups)]
