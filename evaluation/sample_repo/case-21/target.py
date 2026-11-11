from pathlib import Path

def extension(name):
    return Path(name).suffix[1:]
