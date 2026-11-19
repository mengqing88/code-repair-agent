"""Payload validation for the signup endpoint."""

from __future__ import annotations

REQUIRED = {"active": bool, "age": int, "name": str}


def validate(payload):
    """Return a list of human readable validation errors."""
    errors = []
    for field, expected in REQUIRED.items():
        value = payload.get(field)
        if not isinstance(value, expected):
            errors.append("field {0} must be {1}".format(field, expected.__name__))
    return errors
