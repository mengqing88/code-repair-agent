# 简化的支付回调处理器。
# 状态：pending -> paid；pending -> cancelled；paid / cancelled 为终态。


class OrderProcessor:
    def __init__(self):
        self.state = "pending"
        self.total_charged = 0
        self._seen_events = set()

    def apply_payment(self, event_id, amount_cents):
        """处理一次支付回调，返回是否真正入账。"""
        if event_id in self._seen_events:
            return False
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        # 错误：只排除了 cancelled，paid 状态下的新事件仍会被重复入账。
        if self.state != "cancelled":
            self.state = "paid"
            self.total_charged += amount_cents
            self._seen_events.add(event_id)
            return True
        return False

    def cancel(self):
        """仅 pending 可取消，返回是否成功。"""
        if self.state == "pending":
            self.state = "cancelled"
            return True
        return False
