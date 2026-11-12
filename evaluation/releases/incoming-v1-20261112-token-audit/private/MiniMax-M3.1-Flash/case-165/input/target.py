"""工单排序。"""


def sort_orders(orders):
    """按优先级降序、创建时间升序排序工单。"""
    return sorted(orders, key=lambda order: -order["priority"])
