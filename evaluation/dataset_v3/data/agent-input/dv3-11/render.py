"""Placeholder rendering for notification templates."""

from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render(template, values):
    """Replace ``{{name}}`` with ``values[name]``."""
    return _PLACEHOLDER.sub(lambda match: str(values.get(match.group(1), "")), template)
