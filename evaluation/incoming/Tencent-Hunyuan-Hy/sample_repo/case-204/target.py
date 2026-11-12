# 请求去重：同一 request_id 只处理一次。


class Deduper:
    def __init__(self):
        self._seen = set()

    def process(self, request_id, payload, received_at):
        key = (request_id, received_at)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True
