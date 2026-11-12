class OrderState:
    ALLOWED = {'new', 'paid', 'shipped', 'cancelled', 'refunded'}

    def __init__(self, status='new'):
        if status not in self.ALLOWED:
            raise ValueError('unknown status')
        self.status = status

    def transition(self, target):
        if target not in self.ALLOWED:
            raise ValueError('unknown status')
        if self.status == 'refunded':
            raise ValueError('refunded is final')
        if self.status == 'cancelled':
            raise ValueError('cancelled is final')
        if target == 'shipped' and self.status != 'paid':
            raise ValueError('shipped requires paid')
        self.status = target
        return self.status
