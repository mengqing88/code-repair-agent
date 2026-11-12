class Task:
    def __init__(self):
        self.state = 'pending'
    def start(self):
        if self.state == 'pending':
            self.state = 'running'
        else:
            raise RuntimeError('cannot start')
        return self.state
    def finish(self):
        if self.state == 'running':
            self.state = 'done'
        return self.state
