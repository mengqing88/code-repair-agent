"""Durable execution ownership and reversible, task-scoped recovery snapshots."""
from __future__ import annotations

from copy import deepcopy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import re
from uuid import uuid4

from code_repair.resilience import Fault, digest


WORKFLOW_VERSION = 1
AUDIT_FILES = {"errors.jsonl", "trace.jsonl"}
BUSINESS_KEYS = {"repair_cases": "case_id", "repair_reviews": "task_id"}
DISPATCH_READY = ("json_extract(payload, '$.execution') = 'queued' "
                  "AND json_type(payload, '$.progress.run_id') = 'null'")


def boot_identity():
    if os.name == "nt":
        data = ctypes.create_string_buffer(48)
        status = ctypes.windll.ntdll.NtQuerySystemInformation(3, data, len(data), None)
        return str(int.from_bytes(data.raw[:8], "little")) if status == 0 else None
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def initial_progress():
    return {"version": WORKFLOW_VERSION, "revision": 0, "run_id": None, "execution_epoch": 0,
            "next_node": "prepare_workspace", "in_flight": None, "interrupted": False,
            "recovery_count": 0, "recovery": None, "pending_calls": [], "clock": None}


class ServiceLock:
    """An OS lock is released on process death; a stale PID file grants no ownership."""
    def __init__(self, root):
        self.path, self.stream = Path(root) / ".service.lock", None

    def acquire(self):
        self.stream = self.path.open("a+b")
        try:
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            self.stream = None
            raise Fault("service_already_running", "该数据目录已有服务在运行", category="permission") from error

    def release(self):
        if self.stream:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


def file_manifest(root, *, source=False):
    root = Path(root)
    assert_plain_path(root)
    if not root.exists():
        return {}
    result = {}
    ignored = {".git", ".venv", "venv", "work", "node_modules", "__pycache__", ".pytest_cache", ".idea"}
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        if source:
            dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".env")]
            files = [name for name in files if not name.startswith(".env")]
        for name in list(dirs) + files:
            path = parent / name
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise Fault("snapshot_link", "检查点含符号链接或目录联接，已停止恢复", category="permission")
        for name in dirs:
            path = parent / name
            result[path.relative_to(root).as_posix()] = {"directory": True, "mode": path.stat().st_mode & 0o777}
        for name in files:
            relative = (parent / name).relative_to(root).as_posix()
            if (source and (name.startswith(".env") or name == ".git")) or (not source and relative in AUDIT_FILES):
                continue
            path = parent / name
            result[relative] = {"hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "mode": path.stat().st_mode & 0o777}
    return result


def assert_plain_path(path):
    """Do not resolve a link away before checking it, including any existing ancestor."""
    for part in (Path(path), *Path(path).parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise Fault("snapshot_link", "检查点路径含链接或目录联接，已停止恢复", category="permission")


def managed_task_path(store, task_id):
    if not re.fullmatch(r"[a-f0-9]{32}", task_id):
        raise Fault("invalid_task_identity", "恢复任务标识无效", category="validation")
    path = store.root / "tasks" / task_id
    assert_plain_path(path)
    return path


def filesystem_path(path):
    """Use Windows extended paths for nested reproduction reports in checkpoint copies."""
    path = Path(path).absolute()
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def durable_copy(source, destination):
    # Windows FlushFileBuffers requires a writable handle, even when syncing a copy.
    if destination.exists():
        os.chmod(destination, 0o600)
    with Path(source).open("rb") as incoming, destination.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    shutil.copystat(source, destination)


def source_fingerprint(source, root):
    if source["type"] == "git_repository":
        return {"revision": source["revision"]}
    return file_manifest(root, source=True)


class ProgressStore:
    """Mixin for TaskStore. Every progress mutation uses the same guarded task transaction."""
    def list_tasks(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM tasks ORDER BY rowid")]

    def list_queued_tasks(self, limit):
        if limit <= 0:
            return []
        with self.connect() as db:
            rows = db.execute(
                f"SELECT payload FROM tasks WHERE {DISPATCH_READY} ORDER BY rowid LIMIT ?",
                (limit,),
            )
            return [json.loads(row[0]) for row in rows]

    def owns(self, task_id, run_id):
        return self.get(task_id).get("progress", {}).get("run_id") == run_id

    @staticmethod
    def allocate_epoch(db, task):
        """The high-water mark lives outside recoverable task payloads."""
        task_id = task["state"]["task_id"]
        prior = int(task["progress"].get("execution_epoch", 0))
        db.execute("INSERT INTO execution_epochs(task_id,value) VALUES (?,?) "
                   "ON CONFLICT(task_id) DO UPDATE SET value=MAX(value+1,excluded.value)",
                   (task_id, prior + 1))
        epoch = db.execute("SELECT value FROM execution_epochs WHERE task_id=?", (task_id,)).fetchone()[0]
        task["progress"]["execution_epoch"] = epoch
        return epoch

    def claim(self, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = json.loads(db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            progress = task.get("progress")
            if progress is None:
                return task, None
            if task["execution"] != "queued" or progress["run_id"] is not None:
                return None, None
            run_id = uuid4().hex
            self.allocate_epoch(db, task)
            progress["run_id"] = run_id
            self._write(db, task, expected_run=None)
            return task, run_id

    def revoke(self, task):
        old_run = task["progress"]["run_id"]
        if old_run:
            task["progress"].setdefault("revoked_runs", []).append(old_run)
        task["progress"]["run_id"] = None
        with self.connect() as db:
            self._write(db, task, expected_run=old_run)
        return task

    def assert_owner(self, task, run_id):
        saved = self.get(task["state"]["task_id"]).get("progress", {})
        if task.get("progress") and (not run_id or task["progress"]["run_id"] != run_id
                                     or saved.get("run_id") != run_id
                                     or saved.get("execution_epoch", 0) != task["progress"].get("execution_epoch", 0)):
            raise Fault("stale_execution", "旧执行已失去任务写入资格", category="conflict")

    def begin_step(self, task, node, state, run_id):
        self.assert_owner(task, run_id)
        if node != task["progress"]["next_node"]:
            raise Fault("invalid_task_transition", "开始节点与已提交续执行位置不一致", category="conflict")
        # Accounting may have already persisted a lower budget than the graph input.
        budget = task["state"]["remaining_budget"]
        task["state"] = deepcopy(state)
        for key in ("seconds", "tokens", "cost", "attempts"):
            task["state"]["remaining_budget"][key] = min(task["state"]["remaining_budget"][key], budget[key])
        progress = task["progress"]
        progress.update(next_node=node, in_flight={"node": node, "attempt_id": uuid4().hex,
            "input_hash": digest(state), "input_state": deepcopy(state), "code_execution_started": False,
            "model_tool_policy": "journaled" if node == "propose_patch" else "not_applicable"}, interrupted=False)
        self.put(task)

    def finish_step(self, task, node, state, update, next_node, run_id):
        self.assert_owner(task, run_id)
        attempt = task["progress"]["in_flight"]
        if not attempt or attempt["node"] != node:
            raise Fault("checkpoint_conflict", "节点完成记录与执行意图冲突", category="conflict")
        new_state = {**deepcopy(state), **deepcopy(update)}
        from code_repair.repair_workflow import next_node_after
        expected = "baseline_reproduction" if node == "prepare_workspace" else next_node_after(node, new_state)
        if next_node != expected:
            raise Fault("invalid_task_transition", "提交的下一节点不符合工作流路由", category="conflict")
        for key in ("seconds", "tokens", "cost", "attempts"):
            new_state["remaining_budget"][key] = min(new_state["remaining_budget"][key],
                                                   task["state"]["remaining_budget"][key])
        task["state"] = new_state
        progress = task["progress"]
        progress.update(in_flight=None, next_node=next_node, last_attempt=attempt["attempt_id"],
                        committed_state=deepcopy(new_state), state_hash=digest(new_state))
        try:
            self.put(task)
        except Exception:
            # COMMIT may have succeeded even if its acknowledgement was lost.
            saved = self.get(state["task_id"])
            if saved.get("progress", {}).get("last_attempt") != attempt["attempt_id"]:
                raise
            task.update(saved)

    def account(self, task, context):
        self.assert_owner(task, task["progress"]["run_id"])
        budget = task["state"]["remaining_budget"]
        budget["seconds"] = min(budget["seconds"], context.remaining())
        budget["tokens"] = min(budget["tokens"], max(0, context.max_tokens - context.tokens))
        budget["cost"] = min(budget["cost"], max(0, context.max_cost - context.cost))
        prior = context.prior_accounting
        task["state"]["accounting"] = {"tokens_observed": prior.get("tokens_observed", 0) + context.tokens,
            "cost_observed_yuan": prior.get("cost_observed_yuan", 0) + context.cost,
            "cost_complete": context.cost_known, "usage_complete": context.usage_known}
        task["progress"]["clock"] = {"boot": boot_identity(), "tick": time.monotonic(),
                                      "remaining": budget["seconds"]}
        self.put(task)


def setup_recovery(db):
    db.execute("CREATE TABLE IF NOT EXISTS execution_epochs (task_id TEXT PRIMARY KEY,value INTEGER NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS recovery_jobs (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, phase TEXT NOT NULL, snapshot TEXT NOT NULL)""")


class RecoveryBackup:
    """A remains immutable. Interrupted overwrites restart from A, including its business rows."""
    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        with store.connect() as db:
            row = db.execute("SELECT task_id,phase,snapshot FROM recovery_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise Fault("snapshot_missing", "恢复前检查点不存在", category="conflict")
        self.task_id, self.phase, self.snapshot = row[0], row[1], json.loads(row[2])
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise Fault("snapshot_identity_invalid", "恢复快照标识无效", category="validation")
        self.directory = filesystem_path(store.root / "recovery_backups" / self.task_id / job_id)
        self.target = filesystem_path(managed_task_path(store, self.task_id))
        assert_plain_path(self.directory)

    @classmethod
    def capture(cls, store, task):
        task_id, identifier = task["state"]["task_id"], uuid4().hex
        business_path = Path(task["config"].get("knowledge_path", store.root / "knowledge.sqlite3")).resolve()
        snapshot = {"task": deepcopy(task), "manifest": None, "business_path": str(business_path),
                    "business": cls.read_business(business_path, task_id), "writes": []}
        with store.connect() as db:
            db.execute("INSERT INTO recovery_jobs VALUES (?,?,'capturing',?)",
                       (identifier, task_id, json.dumps(snapshot, ensure_ascii=False)))
        backup = cls(store, identifier)
        backup.directory.mkdir(parents=True)
        manifest = file_manifest(backup.target)
        for relative, entry in manifest.items():
            destination = backup.directory / "files" / relative
            if entry.get("directory"):
                destination.mkdir(parents=True, exist_ok=True)
                os.chmod(destination, entry["mode"])
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            durable_copy(backup.target / relative, destination)
        if file_manifest(backup.directory / "files") != manifest or file_manifest(backup.target) != manifest:
            raise Fault("snapshot_changed", "保存检查点时任务文件发生变化", category="conflict")
        backup.snapshot["manifest"] = manifest
        backup.set_phase("prepared")
        return backup

    @staticmethod
    def read_business(path, task_id):
        if not path.exists():
            return {table: None for table in BUSINESS_KEYS}
        db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {table: dict(row) if table in tables and (row := db.execute(f"SELECT * FROM {table} WHERE {key}=?", (task_id,)).fetchone()) else None
                    for table, key in BUSINESS_KEYS.items()}
        finally:
            db.close()

    def set_phase(self, phase):
        with self.store.connect() as db:
            db.execute("UPDATE recovery_jobs SET phase=?,snapshot=? WHERE id=?",
                       (phase, json.dumps(self.snapshot, ensure_ascii=False), self.job_id))
        self.phase = phase

    def write_business(self, table, row):
        """Allow only a declared task row; record its after-image before the actual write."""
        if table not in BUSINESS_KEYS or row.get(BUSINESS_KEYS[table]) != self.task_id:
            raise Fault("recovery_write_denied", "恢复只允许写入当前任务的指定业务记录", category="permission")
        self.snapshot["writes"].append({"table": table, "after": row})
        self.set_phase("working")
        path = Path(self.snapshot["business_path"])
        with sqlite3.connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(f"SELECT * FROM {table} WHERE {BUSINESS_KEYS[table]}=?", (self.task_id,)).fetchone()
            columns = [entry[1] for entry in db.execute(f"PRAGMA table_info({table})")]
            before = self.snapshot["business"].get(table)
            actual = dict(zip(columns, current)) if current else None
            previous = [item["after"] for item in self.snapshot["writes"][:-1] if item["table"] == table]
            if actual != before and (not previous or actual != previous[-1]):
                raise Fault("business_conflict", "业务记录已被其他操作修改，拒绝覆盖", category="conflict")
            self._replace_row(db, table, self.task_id, row)

    @staticmethod
    def _replace_row(db, table, task_id, row):
        key = BUSINESS_KEYS[table]
        db.execute(f"DELETE FROM {table} WHERE {key}=?", (task_id,))
        if row is not None:
            columns = [entry[1] for entry in db.execute(f"PRAGMA table_info({table})")]
            if set(row) != set(columns):
                raise Fault("business_schema_changed", "业务表结构已变化，不能还原旧记录", category="conflict")
            db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                       tuple(row[name] for name in columns))

    def restore(self):
        from code_repair.task_lifecycle import transition_scope
        with transition_scope("recovery"):
            return self._restore()

    def _restore(self):
        manifest = self.snapshot["manifest"]
        if manifest is None or file_manifest(self.directory / "files") != manifest:
            raise Fault("snapshot_corrupt", "恢复前检查点不完整或已损坏，拒绝覆盖", category="conflict")
        self.set_phase("rolling_back")
        # Only restore rows written by this recovery; unrelated writers are never overwritten.
        if self.snapshot["writes"]:
            path = Path(self.snapshot["business_path"])
            with sqlite3.connect(path, timeout=5) as db:
                db.execute("BEGIN IMMEDIATE")
                db.row_factory = sqlite3.Row
                for table in {item["table"] for item in self.snapshot["writes"]}:
                    current = db.execute(f"SELECT * FROM {table} WHERE {BUSINESS_KEYS[table]}=?", (self.task_id,)).fetchone()
                    actual, before = dict(current) if current else None, self.snapshot["business"].get(table)
                    expected = [item["after"] for item in self.snapshot["writes"] if item["table"] == table]
                    if actual != before and actual not in expected:
                        raise Fault("business_conflict", "业务记录存在其他修改，已停止检查点覆盖", category="conflict")
                    self._replace_row(db, table, self.task_id, before)
        current_manifest = file_manifest(self.target)
        # Remove leaves first; also handle a file replaced by a directory or vice versa.
        for relative in sorted(current_manifest, key=lambda value: len(Path(value).parts), reverse=True):
            actual = current_manifest[relative]
            wanted = manifest.get(relative)
            if wanted is None or bool(actual.get("directory")) != bool(wanted.get("directory")):
                path = self.target / relative
                os.chmod(path, 0o700 if actual.get("directory") else 0o600)
                path.rmdir() if actual.get("directory") else path.unlink()
        for relative, evidence in manifest.items():
            destination = self.target / relative
            if evidence.get("directory"):
                destination.mkdir(parents=True, exist_ok=True)
                os.chmod(destination, evidence["mode"])
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(".recovery-" + uuid4().hex + ".tmp")
            if temporary.exists():
                os.chmod(temporary, 0o600)
            durable_copy(self.directory / "files" / relative, temporary)
            if destination.exists():
                os.chmod(destination, 0o600)
            os.replace(temporary, destination)
        if file_manifest(self.target) != manifest:
            raise Fault("restore_incomplete", "检查点文件覆盖尚未完成", category="conflict")
        current = self.store.get(self.task_id)
        original = deepcopy(self.snapshot["task"])
        for key in ("seconds", "tokens", "cost", "attempts"):
            original["state"]["remaining_budget"][key] = min(original["state"]["remaining_budget"][key],
                                                            current["state"]["remaining_budget"][key])
        original["state"]["accounting"] = current["state"].get("accounting", {})
        original["state"]["warnings"] = current["state"].get("warnings", [])
        original["state"]["elapsed_seconds"] = max(original["state"]["elapsed_seconds"], current["state"]["elapsed_seconds"])
        original["progress"]["revision"] = current["progress"]["revision"]
        original["progress"]["execution_epoch"] = current["progress"].get("execution_epoch", 0)
        original["progress"]["run_id"] = None
        for key in ("revoked_runs", "pending_calls", "clock", "agent_calls", "apply_generation"):
            original["progress"][key] = current["progress"].get(key)
        original["progress"]["recovery_count"] = current["progress"]["recovery_count"]
        original["progress"]["recovery"] = {"id": self.job_id, "status": "rolled_back"}
        original["execution"] = "idle"
        with self.store.connect() as db:
            self.store._write(db, original)
            db.execute("UPDATE recovery_jobs SET phase='rolled_back' WHERE id=?", (self.job_id,))
        self.phase = "rolled_back"
        return original
