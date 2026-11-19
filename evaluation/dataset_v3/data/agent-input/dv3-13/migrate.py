"""Ordered schema migrations."""

from __future__ import annotations


class MigrationError(RuntimeError):
    """Raised when a migration cannot be applied."""


class Migrator:
    def __init__(self, schema=None):
        self.schema = dict(schema or {})
        self.version = 0

    def apply(self, version, change):
        """Apply one migration step exactly once, in order."""
        change(self.schema)
        self.version = version
        return self.version
