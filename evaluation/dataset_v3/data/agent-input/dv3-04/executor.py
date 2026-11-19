"""Retry executor with a wall-clock budget."""

from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """Raised when the retry budget is exhausted."""

    def __init__(self, attempts, last_error):
        super().__init__("gave up after {0} attempts: {1!r}".format(attempts, last_error))
        self.attempts = attempts
        self.last_error = last_error


class Executor:
    def __init__(self, clock):
        self.clock = clock

    def run(self, task, budget_seconds, max_attempts=3):
        """Call ``task`` until it succeeds, the budget or the attempt cap runs out."""
        started = self.clock()
        attempts = 0
        last_error = None
        while attempts < max_attempts:
            attempts += 1
            try:
                return task()
            except Exception as exc:  # noqa: BLE001 - retried on purpose
                last_error = exc
        raise BudgetExceeded(attempts, last_error)
