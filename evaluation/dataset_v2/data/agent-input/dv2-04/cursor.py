"""Cursor pagination over a ``(score desc, id asc)`` ordering."""

from __future__ import annotations

import base64
import json


def encode_cursor(ident):
    """Turn a position into an opaque cursor."""
    payload = json.dumps({"id": ident}, sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor):
    """Turn a cursor back into a position."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return payload["id"]
    except Exception:
        raise ValueError("invalid cursor")


def _ordered(rows):
    return sorted(rows, key=lambda row: (-row["score"], row["id"]))


def page(rows, limit, cursor=None):
    """Return one page and the cursor for the next one."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    ordered = _ordered(rows)
    if cursor is not None:
        ident = decode_cursor(cursor)
        ordered = [row for row in ordered if row["id"] > ident]
    chunk = ordered[:limit]
    next_cursor = None
    if len(ordered) > limit and chunk:
        next_cursor = encode_cursor(chunk[-1]["id"])
    return chunk, next_cursor
