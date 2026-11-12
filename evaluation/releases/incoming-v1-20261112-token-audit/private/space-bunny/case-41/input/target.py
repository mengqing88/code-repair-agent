
"""CSV row parsing."""


def parse_row(line):
    """Split a CSV line into fields."""
    if line == "":
        return []
    return line.split(",")
