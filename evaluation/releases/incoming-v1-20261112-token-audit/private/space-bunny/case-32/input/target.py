
"""Half-open interval overlap checks."""


def _validate(interval):
    if interval["end"] < interval["start"]:
        raise ValueError("interval end must not be before start")


def intervals_overlap(a, b):
    """Return True when the half-open intervals [start, end) intersect."""
    _validate(a)
    _validate(b)
    return a["start"] <= b["end"] and b["start"] <= a["end"]
