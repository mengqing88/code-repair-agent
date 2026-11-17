"""Layered configuration loading."""

from __future__ import annotations

from .defaults import DEFAULT_CONFIG
from .merge import deep_merge


def load_config(file_values=None, env_values=None):
    """Layer defaults, file values and environment overrides."""
    config = deep_merge(DEFAULT_CONFIG, file_values or {})
    if env_values:
        config = deep_merge(config, env_values)
    return config
