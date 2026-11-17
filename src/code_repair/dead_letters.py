"""Durable failure isolation; redelivery is an explicit, audited user action."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from code_repair.input_protection import protect_input


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS task_dead_letters (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL,
        failure TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT, actor TEXT)""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_dead_letter ON task_dead_letters(task_id) WHERE status='open'")
    db.execute("""CREATE TABLE IF NOT EXISTS dead_letter_decisions (
        id TEXT NOT NULL, request_id TEXT NOT NULL, parameters TEXT NOT NULL,
        PRIMARY KEY(id,request_id))""")


def capture(db, previous, task):
    state = task["state"]
    fault = state.get("error_info") or {}
    if task["execution"] != "idle" or not (state["status"] in {"failed", "timed_out"}
            or (state["status"] == "needs_input" and (fault.get("outcome_unknown") or fault.get("code") == "agent_no_progress"))):
        return
    if (previous and previous.get("execution") == "idle" and previous["state"].get("status") == state["status"]
            and previous["state"].get("error_info") == state.get("error_info")):
        return
    failure = protect_input({"code": fault.get("code", "task_failed"), "category": fault.get("category", "business"),
                            "message": state.get("last_error"), "outcome_unknown": fault.get("outcome_unknown", False),
                            "node": (task.get("progress") or {}).get("next_node"),
                            "attempt_count": state.get("attempt_count", 0)})
    db.execute("INSERT OR IGNORE INTO task_dead_letters VALUES (?,?,'open',?,?,NULL,NULL)",
               (uuid4().hex, state["task_id"], json.dumps(failure, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))


def list_owned(store, principal, limit=100):
    with store.connect() as db:
        rows = db.execute("SELECT d.id,d.task_id,d.status,d.failure,d.created_at,d.reason "
                          "FROM task_dead_letters d JOIN task_owners o ON o.task_id=d.task_id "
                          "WHERE o.user_id=? ORDER BY d.rowid DESC LIMIT ?", (principal, limit)).fetchall()
    return [{"id": r[0], "task_id": r[1], "status": r[2], "failure": json.loads(r[3]), "created_at": r[4], "reason": r[5]} for r in rows]
