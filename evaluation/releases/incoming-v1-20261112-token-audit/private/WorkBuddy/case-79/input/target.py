class Submit:
    def __init__(self):
        self.cache = {}
    def submit(self, request_id, processor):
        result = processor()
        self.cache[request_id] = result
        return result
