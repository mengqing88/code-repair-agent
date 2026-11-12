# 成绩表排序：score 以十进制字符串存储，按数值升序排列。


def sort_by_score(rows):
    """rows: [(name, score_str)]，返回排序后的新列表。"""
    return sorted(rows, key=lambda r: r[1])
