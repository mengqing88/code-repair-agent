from pathlib import Path

def line_count(path):
    return Path(path).read_text().count('\n')
