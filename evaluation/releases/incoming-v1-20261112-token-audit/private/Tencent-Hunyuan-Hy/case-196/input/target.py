# 解析简单 CSV 行：按逗号切分，字段不做 strip。


def parse_row(line):
    """返回该行的字段列表。"""
    return [p for p in line.split(",") if p]
