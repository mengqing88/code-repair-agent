"""Local accounts, revocable cookie sessions and an explicit, atomic legacy ownership binding."""
from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException


SESSION_COOKIE = "repair_session"
SESSION_SECONDS = 7 * 24 * 3600
_DUMMY_HASH = "scrypt$16384$8$1$" + "0" * 32 + "$" + "0" * 128


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt$16384$8$1${salt}${value}"


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        return hmac.compare_digest(password_hash(password, salt).split("$")[-1], expected)
    except (ValueError, TypeError):
        return False


def csrf_token(session_token):
    return hashlib.sha256(("repair-csrf:" + session_token).encode()).hexdigest()


class UserAccounts:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "workbench.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at REAL NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS task_owners (task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id))")
            db.execute("CREATE INDEX IF NOT EXISTS task_owners_user ON task_owners(user_id)")
            db.execute("CREATE TABLE IF NOT EXISTS account_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS auth_limits (key TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL)")

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def register(self, username, password):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{3,32}", username) or not 10 <= len(password) <= 128:
            raise HTTPException(422, "用户名需为 3–32 位字母、数字或下划线，密码需为 10–128 位")
        user = {"id": uuid4().hex, "username": username}
        encoded = password_hash(password)
        try:
            with self.connect() as db:
                db.execute("INSERT INTO users VALUES (?,?,?,?)", (user["id"], username, encoded, time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该用户名已被使用") from None
        return user

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute("SELECT id,username,password_hash FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        # Do equal password work even when the username does not exist.
        encoded = row[2] if row else _DUMMY_HASH
        valid = verify_password(password, encoded)
        if not row or not valid:
            raise HTTPException(401, "用户名或密码不正确")
        return {"id": row[0], "username": row[1]}

    def limit(self, operation, address, username=""):
        now = time.time()
        keys = [(f"{operation}:ip:{address}", 30), (f"{operation}:name:{username.lower()}", 10)]
        limited = False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_limits WHERE started<?", (now - 600,))
            for key, maximum in keys:
                key = hashlib.sha256(key.encode()).hexdigest()
                row = db.execute("SELECT started,attempts FROM auth_limits WHERE key=?", (key,)).fetchone()
                started, attempts = (row[0], row[1] + 1) if row and now - row[0] < 60 else (now, 1)
                db.execute("INSERT OR REPLACE INTO auth_limits VALUES (?,?,?)", (key, started, attempts))
                limited = limited or attempts > maximum
        if limited:
            raise HTTPException(429, "尝试次数过多，请稍后再试", headers={"Retry-After": "60"})

    def new_session(self, user_id, previous=None):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM user_sessions WHERE expires_at<=?", (time.time(),))
            if previous:
                db.execute("DELETE FROM user_sessions WHERE token_hash=?", (hashlib.sha256(previous.encode()).hexdigest(),))
            db.execute("INSERT INTO user_sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS))
        return token

    def session(self, token):
        if not token or len(token) > 128:
            raise HTTPException(401, "请先登录")
        with self.connect() as db:
            row = db.execute("""SELECT u.id,u.username FROM users u JOIN user_sessions s ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>?""", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "登录已过期，请重新登录")
        return {"id": row[0], "username": row[1]}

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM user_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def exists(self, user_id):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone())

    def owner(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM task_owners WHERE task_id=?", (task_id,)).fetchone()
            return row[0] if row else None

    def legacy_owner(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM account_settings WHERE name='legacy_owner'").fetchone()
            return row[0] if row else None

    def bind_legacy(self, user_id):
        """Operator-only migration; registration never grants access to pre-existing data."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise ValueError("迁移账号不存在")
            previous = db.execute("SELECT value FROM account_settings WHERE name='legacy_owner'").fetchone()
            if previous and previous[0] != user_id:
                raise ValueError("历史数据已有归属，不能重新认领")
            db.execute("INSERT OR IGNORE INTO account_settings VALUES ('legacy_owner',?)", (user_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks' AND type='table'").fetchone():
                db.execute("INSERT OR IGNORE INTO task_owners SELECT id,? FROM tasks", (user_id,))

    def knowledge_path(self, user_id):
        if not re.fullmatch(r"[a-f0-9]{32}", user_id) or not self.exists(user_id):
            raise ValueError("用户身份无效")
        # Keep the legacy file and receipt hashes intact; it now belongs exclusively to this account.
        return self.root / "knowledge.sqlite3" if self.legacy_owner() == user_id else self.root / "users" / user_id / "knowledge.sqlite3"
