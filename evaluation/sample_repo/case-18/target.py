def page(items, offset, limit):
    return items[offset:offset + limit], offset + limit + 1
