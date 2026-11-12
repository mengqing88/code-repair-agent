class EventBuffer:
    def __init__(self, events=[]):
        self.events = events

    def append(self, name):
        self.events.append(name)
        return len(self.events)

    def drain(self):
        items = list(self.events)
        self.events.clear()
        return items

    def clear(self):
        self.events.clear()
