def parse_line(line, sep=','):
    if not line:
        return []
    return [field.strip('"') for field in line.split(sep)]
