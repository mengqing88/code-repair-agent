def make_cache_key(user_id, region):
    """生成缓存键：不同 (user_id, region) 二元组必须对应不同键。"""
    return "{}:{}".format(user_id, region)
