"""状态流转守卫。"""

ALLOWED = {
    "0": {"1"},
    "1": {"2", "9"},
    "2": set(),
    "9": set(),
}


def can_advance(state, target):
    """判断状态能否流转到目标状态。"""
    if not state:
        return False
    return state in ALLOWED.get(target, set())
