"""记录追加。"""


def append_records(records, new_record):
    """把 new_record 追加到 records 并返回新列表。"""
    if not new_record:
        raise ValueError("new_record must be a non-empty dict")
    result = list(records)
    result.append(new_record)
    return result
