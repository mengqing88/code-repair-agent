"""订单状态机。"""

EVENT_TARGET = {
    "PAY": "PAID",
    "CANCEL": "CANCELLED",
    "SHIP": "SHIPPED",
    "DELIVER": "DELIVERED",
}


def next_state(state, event):
    """返回执行事件后的新状态。"""
    if event not in EVENT_TARGET:
        raise ValueError("unknown event: %s" % event)
    return EVENT_TARGET[event]
