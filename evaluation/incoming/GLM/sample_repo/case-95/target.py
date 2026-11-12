def sort_records(records, keys):
    """多字段排序。keys 形如 ("dept", "-salary")：
    约定最左为主键，'-' 前缀表示该字段降序。返回新列表。"""
    result = list(records)
    for key in keys:
        desc = key.startswith("-")
        field = key.lstrip("-")
        result.sort(key=lambda r: r[field], reverse=desc)
    return result
