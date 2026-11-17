"""Retry helper used by the outbound client."""

from __future__ import annotations


class RetryError(RuntimeError):
    """Raised when every allowed attempt failed."""

    def __init__(self, attempts, last_error):
        super().__init__("failed after {0} attempts: {1!r}".format(attempts, last_error))
        self.attempts = attempts
        self.last_error = last_error


def retry_call(fn, attempts=3, delays=None, sleep=None):
    """Call ``fn`` up to ``attempts`` times in total."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    wait = sleep if sleep is not None else (lambda seconds: None)
    pauses = list(delays or [])
    last_error = None
    for index in range(attempts - 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retried on purpose
            last_error = exc
            wait(pauses[index] if index < len(pauses) else 0)
    raise RetryError(attempts, last_error)
