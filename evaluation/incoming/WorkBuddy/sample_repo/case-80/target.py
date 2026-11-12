class Workflow:
    def __init__(self):
        self.state = 'init'
    def step(self, name):
        self.state = name
        return self.state
