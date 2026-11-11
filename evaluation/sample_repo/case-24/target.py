def unique_ids(items):
    seen = set()
    result = []
    for item in items:
        if item['id'] in seen:
            continue
        result.append(item)
    return result
