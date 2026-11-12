def validate_rows(rows):
    """校验 (id, age) 行：id 为空 -> "empty id"；age 不在 0..150 -> "bad age"。
    返回所有违规的 (下标, 原因) 列表，按行号升序；全部合法返回 []。"""
    errors = []
    for i, (uid, age) in enumerate(rows):
        if uid == "":
            return [(i, "empty id")]
        if not (0 <= age <= 150):
            return [(i, "bad age")]
    return errors
