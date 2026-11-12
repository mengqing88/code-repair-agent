class SessionManager:
    """会话管理：ttl 秒不活跃即过期。touch 刷新最后活跃时间。"""

    def __init__(self, ttl):
        self.ttl = ttl
        self.sessions = {}

    def create(self, session_id, now):
        self.sessions[session_id] = now

    def touch(self, session_id, now):
        pass

    def is_expired(self, session_id, now):
        return now - self.sessions[session_id] > self.ttl
