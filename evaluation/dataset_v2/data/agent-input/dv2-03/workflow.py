"""Order workflow state machine."""

from __future__ import annotations


class TransitionError(ValueError):
    """Raised when a transition is not allowed."""


STATES = ("draft", "submitted", "approved", "shipped", "done", "cancelled")

ALLOWED = {
    "draft": frozenset({"submitted", "cancelled"}),
    "submitted": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"shipped", "cancelled"}),
    "shipped": frozenset({"done"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}


class OrderWorkflow:
    def __init__(self):
        self.state = "draft"
        self.reminders = []

    def schedule(self, code):
        self.reminders.append(code)
        return code

    def transition(self, target):
        if target not in STATES:
            raise TransitionError("unknown state: {0}".format(target))
        self.state = target
        if target == "cancelled":
            self.reminders = self.reminders[:1]
        return self.state
