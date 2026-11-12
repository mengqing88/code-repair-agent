class RoundRobin:
    """轮询调度：第一次调用返回 servers[0]，之后依次循环。"""

    def __init__(self, servers):
        self.servers = list(servers)
        self.idx = 0

    def next_server(self):
        self.idx = (self.idx + 1) % len(self.servers)
        return self.servers[self.idx]
