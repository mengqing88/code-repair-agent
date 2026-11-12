def resolve(base, override):
    result = dict(base)
    for k, v in override.items():
        result[k] = v
    return result
