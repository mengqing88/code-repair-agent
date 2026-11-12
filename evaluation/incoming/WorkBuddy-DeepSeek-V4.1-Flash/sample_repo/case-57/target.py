def parse_fields(text, sep=';'):
    if not text:
        return []
    parts = text.split(sep)
    return [part for part in parts if part != '']
