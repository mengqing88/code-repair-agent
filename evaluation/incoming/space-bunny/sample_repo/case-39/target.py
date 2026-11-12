
"""Record chunking."""


def chunk_records(records, size):
    """Split records into groups of at most ``size`` items."""
    if not isinstance(size, int) or isinstance(size, bool):
        raise TypeError("size must be an int")
    if size <= 0:
        raise ValueError("size must be positive")
    items = list(records)
    chunks = []
    for start in range(0, len(items) - size + 1, size):
        chunks.append(items[start:start + size])
    return chunks
