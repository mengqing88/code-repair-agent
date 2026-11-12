
"""Configuration merging."""


def merge_configs(base, override):
    """Return a new dict combining base and override configurations."""
    merged = dict(base)
    merged.update(override)
    return merged
