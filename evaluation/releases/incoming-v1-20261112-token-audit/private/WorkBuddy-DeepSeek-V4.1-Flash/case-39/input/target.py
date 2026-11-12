class IdempotentService:
    def __init__(self, handler):
        self.handler = handler
        self.results = {}

    def handle(self, key, payload):
        if key in self.results:
            self.results[key] = self.handler(payload)
            return self.results[key]
        result = self.handler(payload)
        return result
