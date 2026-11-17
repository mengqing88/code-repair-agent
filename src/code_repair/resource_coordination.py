"""Shared PostgreSQL claims. Unknown effects never lose a lock to lease expiry."""
import psycopg
from uuid import uuid4

from code_repair.resilience import Fault


class PostgresResourceCoordinator:
    def __init__(self, dsn):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, connect_timeout=5, options="-c statement_timeout=5000")

    @staticmethod
    def setup(db):
        db.execute("SELECT pg_advisory_xact_lock(1948217301)")
        db.execute("CREATE SEQUENCE IF NOT EXISTS code_repair_resource_fence")
        db.execute("CREATE TABLE IF NOT EXISTS code_repair_coordination_identity (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
        db.execute("INSERT INTO code_repair_coordination_identity VALUES (1,%s) ON CONFLICT DO NOTHING", (uuid4().hex,))
        db.execute("""CREATE TABLE IF NOT EXISTS code_repair_resource_claims (
            resource_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL, operation_key TEXT NOT NULL,
            task_id TEXT NOT NULL, fence_token BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def identity(self):
        try:
            with self.connect() as db:
                self.setup(db)
                return db.execute("SELECT value FROM code_repair_coordination_identity WHERE id=1").fetchone()[0]
        except Exception as error:
            raise Fault("coordination_unavailable", "共享协调数据库身份无法核对，已阻止外部写入", category="storage") from error

    def acquire(self, resource_key, owner_id, operation_key, task_id):
        try:
            with self.connect() as db:
                self.setup(db)
                db.execute("""INSERT INTO code_repair_resource_claims
                    (resource_key,owner_id,operation_key,task_id,fence_token)
                    VALUES (%s,%s,%s,%s,nextval('code_repair_resource_fence')) ON CONFLICT DO NOTHING""",
                           (resource_key, owner_id, operation_key, task_id))
                row = db.execute("SELECT owner_id,operation_key,task_id,fence_token FROM code_repair_resource_claims "
                                 "WHERE resource_key=%s FOR UPDATE", (resource_key,)).fetchone()
                if row[:3] != (owner_id, operation_key, task_id):
                    raise Fault("external_resource_busy", "另一执行节点持有共享资源，当前操作未执行", category="conflict")
                return row[3]
        except Fault:
            raise
        except Exception as error:
            raise Fault("coordination_unavailable", "共享协调数据库不可用，已阻止外部写入", category="storage") from error

    def release(self, resource_key, owner_id, operation_key, fence_token):
        try:
            with self.connect() as db:
                self.setup(db)
                db.execute("DELETE FROM code_repair_resource_claims WHERE resource_key=%s AND owner_id=%s "
                           "AND operation_key=%s AND fence_token=%s", (resource_key, owner_id, operation_key, fence_token))
        except Exception as error:
            raise Fault("coordination_release_failed", "共享资源锁尚未释放，已保留待处理记录", category="storage") from error
