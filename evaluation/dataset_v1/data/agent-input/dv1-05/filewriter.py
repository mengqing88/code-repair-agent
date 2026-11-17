"""Atomic file writing for the export pipeline."""

from __future__ import annotations

import os
import uuid


class AtomicWriteError(RuntimeError):
    """Raised when the payload cannot be written."""


def write_atomic(directory, name, chunks):
    """Write ``chunks`` to ``directory/name`` through a temporary file."""
    target = os.path.join(directory, name)
    tmp = os.path.join(directory, ".{0}.{1}.tmp".format(name, uuid.uuid4().hex))
    handle = open(tmp, "w", encoding="utf-8")
    try:
        for chunk in chunks:
            if not isinstance(chunk, str):
                raise AtomicWriteError("chunk must be str")
            handle.write(chunk)
    except BaseException:
        handle.close()
        raise
    handle.close()
    os.replace(tmp, target)
    return target
