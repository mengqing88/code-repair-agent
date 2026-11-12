class Refund:
    def __init__(self):
        self.state = 'paid'
    def transition(self, action):
        if self.state == 'paid' and action == 'partial_refund':
            self.state = 'partially_refunded'
        elif self.state == 'paid' and action == 'refund':
            self.state = 'refunded'
        else:
            raise ValueError('invalid transition')
        return self.state
