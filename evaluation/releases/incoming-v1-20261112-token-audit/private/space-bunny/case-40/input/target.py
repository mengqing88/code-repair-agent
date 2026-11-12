
"""Configuration resolution."""


def resolve_config(defaults, file_values, env_values):
    """Merge defaults, file values and environment values by priority."""
    merged = dict(defaults)
    for layer in (file_values, env_values):
        for key, value in layer.items():
            merged[key] = value
    return merged
