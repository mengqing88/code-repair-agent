def dedupe(items):
    seen = set()
    result = []
    for x in reversed(items):
        if x not in seen:
            seen.add(x)
            result.append(x)
    result.reverse()
    return result
