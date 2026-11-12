"""工单状态流转。"""

ALLOWED = {
    "PENDING": {"PENDING_REVIEW", "CANCELLED"},
    "PENDING_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"CLOSED"},
    "CLOSED": set(),
}


def can_transition(current, target):
    """判断 current 是否可以流转到 target。"""
    for state, targets in ALLOWED.items():
        if current.startswith(state):
            return target in targets
    return False
