"""Layered settings resolution."""

from __future__ import annotations

DEFAULTS = {"retries": 3, "region": "us", "timeout": 30}


def resolve(file_values=None, env_values=None):
    """Layer defaults, file values and environment overrides."""
    settings = dict(DEFAULTS)
    for source in (file_values or {}, env_values or {}):
        for key, value in source.items():
            if not value:
                continue
            settings[key] = value
    return settings
