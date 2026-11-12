def page_of(items, page, size):
    if page < 0:
        raise ValueError('page must be non-negative')
    start = (page - 1) * size
    total = (len(items) + size - 1) // size
    return items[start:start + size], total
