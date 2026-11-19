"""Idempotent request handling for the public API."""

from __future__ import annotations

import hashlib
import json


class IdempotencyStore:
    def __init__(self):
        self._records = {}

    def _key(self, method, path, body):
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def handle(self, method, path, body, execute):
        key = self._key(method, path, body)
        if key in self._records:
            return dict(self._records[key])
        response = execute()
        self._records[key] = dict(response)
        return response

    def __len__(self):
        return len(self._records)
