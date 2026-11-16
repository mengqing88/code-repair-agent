"""Native LangGraph persistence with the same execution fence as business writes."""
from contextlib import contextmanager
import json
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from code_repair.resilience import Fault


class FencedSqliteSaver(SqliteSaver):
    def __init__(self, connection, store, task_id, run_id, epoch):
        super().__init__(connection)
        self.store, self.task_id, self.run_id, self.epoch = store, task_id, run_id, epoch

    @contextmanager
    def fence(self):
        # Hold the task DB write lock until the checkpoint commit has completed.
        # A concurrent revoke/claim therefore orders before or after this write.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (self.task_id,)).fetchone()
            progress = json.loads(row[0]).get("progress", {}) if row else {}
            if progress.get("run_id") != self.run_id or progress.get("execution_epoch", 0) != self.epoch:
                raise Fault("stale_execution", "旧执行不能写入 LangGraph 检查点", category="conflict")
            yield

    def put(self, config, checkpoint, metadata, new_versions):
        with self.fence():
            return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.fence():
            return super().put_writes(config, writes, task_id, task_path)


@contextmanager
def task_checkpointer(directory, store, task):
    """Each epoch starts at a reconciled business checkpoint, never an uncertain effect."""
    connection = sqlite3.connect(directory / "langgraph.sqlite3", check_same_thread=False, timeout=10)
    try:
        progress = task["progress"]
        saver = FencedSqliteSaver(connection, store, task["state"]["task_id"],
                                  progress["run_id"], progress.get("execution_epoch", 0))
        yield saver, {"configurable": {"thread_id": f"{task['state']['task_id']}:{progress.get('execution_epoch', 0)}"},
                      "recursion_limit": 100}
    finally:
        connection.close()
