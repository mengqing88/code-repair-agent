def add_item(item, bucket=[]):
    """把 item 加入 bucket 并返回该列表；bucket 未提供时使用全新空列表。"""
    bucket.append(item)
    return bucket
