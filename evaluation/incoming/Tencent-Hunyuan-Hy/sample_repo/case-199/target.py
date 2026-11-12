# 分页：计算总页数。


def page_count(total_items, page_size):
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return total_items // page_size
