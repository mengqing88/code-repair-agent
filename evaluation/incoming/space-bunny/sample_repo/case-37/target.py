
"""Order state machine."""

VALID_STATES = {"pending", "paid", "shipped", "cancelled"}


class InvalidTransitionError(ValueError):
    """Raised when a state transition is not allowed."""


class Order:
    def __init__(self, status="pending"):
        if status not in VALID_STATES:
            raise ValueError("unknown status: %s" % status)
        self.status = status

    def transition_to(self, new_status):
        if new_status not in VALID_STATES:
            raise ValueError("unknown status: %s" % new_status)
        self.status = new_status
        return self.status
