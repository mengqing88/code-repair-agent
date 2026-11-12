def parse_version(text):
    if not text:
        raise ValueError('empty version')
    return tuple(text.split('.'))
