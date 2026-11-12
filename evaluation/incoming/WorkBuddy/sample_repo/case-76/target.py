class Order:
    def __init__(self):
        self.state = 'created'
    def transition(self, action):
        if self.state == 'created' and action == 'pay':
            self.state = 'paid'
        elif self.state == 'paid' and action == 'ship':
            self.state = 'shipped'
        elif self.state == 'paid' and action == 'pay':
            raise ValueError('already paid')
        else:
            raise ValueError('invalid transition')
        return self.state
