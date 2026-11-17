"""Sandboxed file storage."""

from __future__ import annotations

import os


class Storage:
    """Resolve user supplied names inside a fixed root directory."""

    def __init__(self, root):
        self.root = os.path.realpath(root)

    def resolve(self, name):
        if ".." in name:
            raise PermissionError("path escapes storage root: {0}".format(name))
        return os.path.join(self.root, name)
