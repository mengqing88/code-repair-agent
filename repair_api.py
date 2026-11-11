"""Local HTTP adapter for LangGraph; durable snapshots and human review.

Run a single worker with uvicorn repair_api:app --host 127.0.0.1 --port 8000.
Source code and pytest run in the constrained Docker sandbox by default.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from threading import Lock, Event, Thread
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_cli import check_source
from knowledge_base import KnowledgeBase
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace, restore_checkpoint_workspace
from repair_workflow import build_repair_workflow, next_node_after
from recovery_store import (ProgressStore, initial_progress, setup_recovery, ServiceLock,
                            source_fingerprint, file_manifest, boot_identity)
from reproduction import reproduce
from operation_store import OperationStore
from docker_executor import cleanup_docker_task, get_execution_backend
from resilience import (Fault, digest, error_info, trace, execution_scope, current,
                        cleanup_scope, run_process, process_identity, stop_recorded_worker, rollback_error, failure_update,
                        trace_workflow_end)
from trace_store import read_trace_page
from diagnostics import diagnostic_scope, process_evidence, record_error, redact


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["python_file", "git_repository", "pasted_code"]
    path: str = Field(default="", max_length=4096)
    revision: str = Field(default="", max_length=64)
    code_text: str = Field(default="", max_length=1_000_000)
    file_name: str = Field(default="", max_length=255)
    file_content: str | None = Field(default=None, max_length=1_000_000)
    error_report: str = Field(min_length=1, max_length=100_000)
    test_target: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_input(self):
        if not self.error_report.strip():
            raise ValueError("错误报告不能为空")
        if self.source_type == "pasted_code" and not self.code_text.strip():
            raise ValueError("Python 代码不能为空")
        if self.source_type != "pasted_code" and not self.path.strip() and self.file_content is None:
            raise ValueError("请提供本地路径或上传文件")
        if self.file_content is not None:
            name = self.file_name
            if self.source_type != "python_file" or not self.file_content.strip():
                raise ValueError("只有文件模式可上传非空文件")
            if not name or name in (".", "..") or any(c in name for c in '/\\:'):
                raise ValueError("上传文件名必须是普通文件名")
            if name.startswith(".") or name.endswith((".", " ")):
                raise ValueError("上传文件名不能以点开头或以点、空格结尾")
            if Path(name).is_reserved():
                raise ValueError("不能使用系统保留文件名")
        if self.source_type != "git_repository" and self.revision:
            raise ValueError("固定版本仅适用于 Git 仓库")
        return self


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=2000)
    patch_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[dict] = Field(min_length=1, max_length=100)


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.35, ge=0, le=1)


class ClientFaultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["component_error", "browser_error", "unhandled_rejection", "resource_error", "storage_error",
                  "request_timeout", "connection_failed", "response_invalid"]
    error_id: str = Field(pattern=r"^[a-z0-9-]{8,64}$")
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


_SAME_RUN = object()


class TaskStore(ProgressStore):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workbench.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, params_hash TEXT NOT NULL, task_id TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS repair_reviews (
                task_id TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL,
                patch_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
            setup_recovery(db)

    @contextmanager
    def connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10)
            with db:
                yield db
        except sqlite3.Error as error:
            error_info(error, operation="task_store.database")
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _write(db, task, *, expected_run=_SAME_RUN):
        task["updated_at"] = timestamp()
        task_id = task["state"]["task_id"]
        saved = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        previous = json.loads(saved[0]) if saved else None
        progress = task.get("progress")
        old = previous.get("progress") if previous else None
        if old:
            owner = progress.get("run_id") if progress and expected_run is _SAME_RUN else expected_run
            if not progress or old["revision"] != progress["revision"] or old["run_id"] != owner:
                raise Fault("stale_execution", "任务版本或执行身份已变化，拒绝旧数据覆盖", category="conflict")
            progress["revision"] += 1
        elif previous and progress:
            raise Fault("checkpoint_version_conflict", "不能直接覆盖旧版任务协议", category="conflict")
        payload = json.dumps(task, ensure_ascii=False)
        if saved:
            result = db.execute("UPDATE tasks SET payload=? WHERE id=? AND payload=?", (payload, task_id, saved[0]))
            if result.rowcount != 1:
                raise Fault("stale_execution", "任务已被其他执行更新", category="conflict")
        else:
            db.execute("INSERT INTO tasks VALUES (?,?)", (task_id, payload))

    def put(self, task):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._write(db, task)

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        return json.loads(row[0])

    def record_review(self, task, review):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO repair_reviews VALUES (?, ?, ?, ?, ?)", (
                task["state"]["task_id"], review["decision"], review["reason"],
                review["patch_hash"], review["created_at"],
            ))
            self._write(db, task)

    def request_task(self, key, parameters=None):
        with self.connect() as db:
            row = db.execute("SELECT params_hash, task_id FROM requests WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if parameters is not None and row[0] != digest(parameters):
            raise HTTPException(409, "幂等键已用于不同任务参数")
        return self.get(row[1])

    def create_once(self, task, key, parameters):
        with self.connect() as db:
            db.execute("INSERT INTO requests VALUES (?, ?, ?)", (key, digest(parameters), task["state"]["task_id"]))
            self._write(db, task)

    def record_worker(self, task_id, process, token):
        identity = process_identity(process.pid)
        if identity is None:
            raise Fault("worker_start_failed", "无法确认执行进程身份", category="internal")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = json.loads(db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
            if task.get("progress") and task["progress"]["run_id"] != token:
                raise Fault("stale_execution", "worker 已失去执行资格", category="conflict")
            task["worker"] = {"pid": process.pid, "identity": identity, "token": token}
            self._write(db, task)

    def recover_interrupted(self):
        with self.connect() as db:
            tasks = [json.loads(row[0]) for row in db.execute("SELECT payload FROM tasks")]
            for task in tasks:
                if task.get("progress"):
                    continue  # Modern tasks use the reversible recovery coordinator.
                if task["execution"] != "idle" or task.get("worker_stop_unconfirmed"):
                    task_id = task["state"]["task_id"]
                    with diagnostic_scope(path=self.root / "tasks" / task_id / "errors.jsonl", task_id=task_id):
                        try:
                            self._recover_task(db, task)
                        except Exception as error:
                            error_info(error, operation="task.recovery")
                            raise

    def _recover_task(self, db, task):
        stop_recorded_worker(task.get("worker"))
        if task.get("config", {}).get("execution_backend", "host") == "docker":
            if not cleanup_docker_task(task["state"]["task_id"]):
                raise Fault("sandbox_cleanup_failed", "恢复前无法确认任务容器已停止", category="unknown",
                            outcome_unknown=True, next_action="先核对任务容器，再恢复副本",
                            causes=[task["state"]["error_info"]] if task["state"].get("error_info") else [])
        task["worker_stop_unconfirmed"] = False
        task["execution"] = "idle"
        if not reconcile_review(task, self.root):
            task["state"].update(status="needs_input" if task.get("review") else "failed",
                last_error="服务重启中断了任务，未自动重放操作，请核对后重试。",
                error_info=error_info(Fault("outcome_unknown" if task.get("review") else "interrupted",
                    "服务重启中断了任务", category="unknown" if task.get("review") else "internal",
                    outcome_unknown=bool(task.get("review")), next_action="人工核对执行记录后处理"), operation="task.recovery"))
            config = task.get("config", {})
            if not task.get("review") and config.get("checkpoint"):
                try:
                    with cleanup_scope():
                        restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                except Exception as error:
                    task["state"].update(failure_update(rollback_error(task["state"].get("error_info"), error)))
        self._write(db, task)


def reconcile_review(task, root):
    """Query the recorded effect and target database before deciding an interrupted review's outcome."""
    review = task.get("review")
    if not review:
        return False
    state, config = task["state"], task["config"]
    if review["decision"] == "accepted":
        path = Path(config.get("knowledge_path", root / "knowledge.sqlite3"))
        if path.exists():
            db = sqlite3.connect(path)
            try:
                row = db.execute("SELECT document FROM repair_cases WHERE case_id=?", (state["task_id"],)).fetchone()
                if row and json.loads(row[0]).get("patch_hash") == review["patch_hash"]:
                    state.update(status="succeeded", memory_saved=True, last_error=None, error_info=None)
                    return True
            except (sqlite3.Error, ValueError) as error:
                error_info(error, operation="review.reconcile.accepted")
            finally:
                db.close()
    if review["decision"] == "rejected":
        path = Path(config.get("knowledge_path", root / "knowledge.sqlite3"))
        if path.exists():
            db = sqlite3.connect(path)
            try:
                row = db.execute("SELECT document FROM repair_reviews WHERE task_id=?", (state["task_id"],)).fetchone()
                if row and json.loads(row[0]).get("patch_hash") == review["patch_hash"]:
                    state.update(status="failed", memory_saved=False, last_error="用户拒绝了候选修复补丁", error_info=None)
                    return True
            except (sqlite3.Error, ValueError) as error:
                error_info(error, operation="review.reconcile.rejected")
            finally:
                db.close()
    key = config.get("effect_keys", {}).get("save_case" if review["decision"] == "accepted" else "reject_patch")
    result = OperationStore(root / "operations.sqlite3").lookup(key) if key else None
    if review["decision"] == "rejected" and result and result["status"] == "completed":
        state.update(status="failed", memory_saved=False, last_error="用户拒绝了候选修复补丁", error_info=None)
        return True
    return False


class RepairRunner:
    def __init__(self, root: Path):
        self.store = TaskStore(root)
        self.lock = Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="repair")
        self.knowledge = KnowledgeBase(self.store.root / "knowledge.sqlite3")
        self.operations = OperationStore(self.store.root / "operations.sqlite3")
        self.durable_operations = OperationStore(self.store.path)
        self.execution_backend = get_execution_backend()
        self.worker_mode = "process"
        self.dispatch_stop = Event()
        self.dispatch_thread = None
        self.submitted = set()
        self.submit_lock = Lock()
        self.service_lock = ServiceLock(self.store.root)

    def create(self, request: TaskInput, key: str | None = None):
        key = key or uuid4().hex
        if not 8 <= len(key) <= 128 or not all(c.isalnum() or c in "-_" for c in key):
            raise HTTPException(422, "幂等键需为 8–128 位字母、数字、连字符或下划线")
        with self.lock:
            existing = self.store.request_task(key, request.model_dump())
            if existing:
                return existing
            return self._create(request, key)

    def _create(self, request, key):
        task_id = uuid4().hex
        directory = self.store.root / "tasks" / task_id
        input_dir = directory / "input"
        input_dir.mkdir(parents=True)
        source_path = Path(request.path.strip()).expanduser().resolve()
        if request.file_content is not None:
            source_path = input_dir / request.file_name
            source_path.write_text(request.file_content, encoding="utf-8")
        args = argparse.Namespace(
            python_file=source_path if request.source_type == "python_file" else None,
            repository=source_path if request.source_type == "git_repository" else None,
            code_text=request.code_text, revision=request.revision.strip() or None,
        )
        try:
            source = check_source(args)
        except (ValueError, OSError) as error:
            error_info(error, operation="task.input")
            raise HTTPException(422, "输入来源校验失败，请检查路径、文件编码或版本参数") from error
        source_root = (source_path if request.source_type == "git_repository" else
                       source_path.parent if request.source_type == "python_file" else input_dir)
        state = create_initial_state(task_id, source, request.error_report,
                                     code_text=request.code_text if request.source_type == "pasted_code" else None)
        task = {
            "state": state, "execution": "queued", "review": None,
            "progress": initial_progress(),
            "source_label": request.file_name or request.path or "粘贴的 Python 代码",
            "config": {"source_root": str(source_root), "workspace": str(directory / "workspace"),
                       "test_targets": [request.test_target.strip() or "."], "checkpoint": None,
                       "execution_backend": self.execution_backend,
                       "effect_keys": {name: uuid4().hex for name in ("apply_patch", "save_case", "reject_patch")},
                       "principal": "local-user",
                       "source_fingerprint": source_fingerprint(source, source_root),
                       "allowed_paths": [source_path.name] if request.source_type == "python_file" else
                                        ["target.py"] if request.source_type == "pasted_code" else ["."]},
            "created_at": timestamp(), "updated_at": timestamp(),
        }
        self.store.create_once(task, key, request.model_dump())
        trace("task.create", "completed", key=key, task_id=task_id, path=directory / "trace.jsonl")
        self.submit(task_id, False)
        return self.store.get(task_id)

    def submit(self, task_id, reviewing):
        with self.submit_lock:
            if task_id in self.submitted:
                return
            task, run_id = self.store.claim(task_id)
            if task is None:
                return
            self.submitted.add(task_id)
        try:
            future = self.pool.submit(self.run, task_id, reviewing, run_id)
        except Exception as error:
            self.submitted.discard(task_id)
            self.background_failure(task_id, error, "task.enqueue", reviewing=reviewing, run_id=run_id)
            raise Fault("enqueue_failed", "任务已保存，但调度失败，请查询原任务状态", category="internal",
                        outcome_unknown=True, causes=[error_info(error, operation="task.enqueue")]) from error
        if future is not None:
            def completed(result):
                try:
                    result.result()
                except BaseException as error:
                    self.background_failure(task_id, error, "task.background", reviewing=reviewing, run_id=run_id)
                finally:
                    self.submitted.discard(task_id)
            future.add_done_callback(completed)
        else:
            self.submitted.discard(task_id)

    def start_dispatcher(self):
        def dispatch():
            while not self.dispatch_stop.wait(0.5):
                try:
                    for task in self.store.list_tasks():
                        progress = task.get("progress")
                        if progress and task["execution"] == "queued" and progress["run_id"] is None:
                            self.submit(task["state"]["task_id"], bool(task.get("review")))
                except Exception as error:
                    error_info(error, operation="task.dispatch")
        self.dispatch_thread = Thread(target=dispatch, name="repair-dispatch", daemon=True)
        self.dispatch_thread.start()

    def background_failure(self, task_id, error, operation, *, reviewing=False, run_id=None):
        path = self.store.root / "tasks" / task_id / "errors.jsonl"
        with diagnostic_scope(path=path, task_id=task_id, operation=operation):
            info = error_info(error, operation=operation)
            try:
                task = self.store.get(task_id)
                if task.get("progress"):
                    if run_id and not self.store.owns(task_id, run_id):
                        return
                    task["progress"]["interrupted"] = True
                if reviewing or task.get("review") or task.get("worker_stop_unconfirmed"):
                    error = Fault("outcome_unknown", "后台执行中断，需先核对副作用结果", category="unknown",
                                  outcome_unknown=True, causes=[info])
                    info = error_info(error, operation=operation)
                task["execution"] = "idle"
                task["state"].update(failure_update(error, operation=operation))
                self.store.put(task)
                trace_workflow_end(task["state"], path=path.with_name("trace.jsonl"), run_id=run_id)
            except Exception as persistence_error:
                error_info(Fault("task_failure_persist_failed", "执行失败且任务失败状态无法保存", category="storage",
                                 outcome_unknown=True, causes=[info, error_info(persistence_error)]),
                           operation="task.failure_persist")

    def services(self, task):
        config = task["config"]
        config["knowledge_path"] = str(self.knowledge.path)
        workspace = Path(config["workspace"])
        toolbox = RepairToolbox(
            workspace_root=workspace, source_root=Path(config["source_root"]),
            allowed_paths=tuple(config["allowed_paths"]), test_targets=tuple(config["test_targets"]),
            checkpoint_revision=config["checkpoint"], knowledge_base=self.knowledge,
            validation_report_path=workspace.parent / "validation.md",
            execution_backend=get_execution_backend(config.get("execution_backend", "host")),
            task_id=task["state"]["task_id"],
        )

        def baseline(state):
            # Reproduce exactly the frozen checkpoint, including supporting files and tests.
            record = reproduce(
                {"type": "git_repository", "path": str(workspace), "revision": config["checkpoint"],
                 "analysis_paths": config["allowed_paths"] if state["source"]["type"] == "python_file" else []},
                state["original_report"], work_root=workspace.parent / "reproduction",
                timeout=min(10.0, max(0.1, state["remaining_budget"]["seconds"])),
                execution_backend=config.get("execution_backend", "host"),
                task_id=state["task_id"],
            )
            return {"matched": record.get("status") == "已复现，与报告一致", "record": record,
                    "question": record.get("question") or None}

        def context(state):
            result = toolbox.collect_context_for_state(state)
            if not result["code_evidence"]:
                size = 0
                for path in sorted(workspace.rglob("*.py")):
                    relative = path.relative_to(workspace)
                    if any(part.startswith(".") or part in ("work", "node_modules", "__pycache__", "venv")
                           for part in relative.parts):
                        continue
                    data = toolbox.read_code(relative.as_posix(), max_bytes=100_000)
                    quote = str(data["content"])
                    if size + len(quote) > 100_000:
                        break
                    size += len(quote)
                    result["code_evidence"].append({"path": relative.as_posix(), "start_line": 1,
                        "end_line": max(1, len(quote.splitlines())), "quote": quote, "reason": "固定工作区源码"})
            return result

        def validate(state):
            toolbox.test_timeout_seconds = min(300.0, max(0.1, state["remaining_budget"]["seconds"]))
            return toolbox.validate_patch_for_state(state)

        services = replace(toolbox.as_repair_services(baseline), collect_context=context, validate_patch=validate)
        regression = (task["state"].get("reproduction") or {}).get("regression")
        if regression:
            toolbox._regression_case = {**regression,
                "reproduction_code": (task["state"].get("reproduction") or {}).get("reproduction_code", "")}
        def effect(name, state, action):
            keys = config.setdefault("effect_keys", {})
            key = keys.setdefault(name, uuid4().hex)
            if name == "apply_patch":
                key += f":{state['attempt_count']}:{state['patch_hash']}"
            if config.get("principal", "local-user") != "local-user":
                raise Fault("permission_denied", "当前任务执行身份不受支持", category="permission")
            if name != "apply_patch" and (not task.get("review") or task["review"]["patch_hash"] != state["patch_hash"]
                    or task["review"]["decision"] != ("accepted" if name == "save_case" else "rejected")):
                raise Fault("review_required", "该副作用需要与补丁版本一致的人工审核记录", category="permission")
            if task.get("progress"):
                from recovery_manager import effect_key, effect_parameters, probe_business
                from operation_store import business_effect
                self.store.assert_owner(task, task["progress"]["run_id"])
                key, parameters = effect_key(task, name, state), effect_parameters(task, name, state)
                self.store.put(task)
                def verified():
                    self.store.assert_owner(task, task["progress"]["run_id"])
                    with business_effect(key, state["task_id"], name, parameters):
                        return action()
                return self.durable_operations.execute(key, state["task_id"], name, parameters, verified,
                    lambda: (probe_business(task, name, state) == "completed", None) if name != "apply_patch" else (False, None))
            self.store.put(task)  # checkpoint intent/key before any side effect
            def reconcile():
                if name in ("save_case", "reject_patch"):
                    probe = {**task, "state": dict(state)}
                    if reconcile_review(probe, self.store.root):
                        return True, None
                return False, None
            trace(name, "authorized", key=key, decision="fixed_node:workspace_write" if name == "apply_patch" else "human_review:database_write")
            return self.operations.execute(key,
                state["task_id"], name, {"patch_hash": state["patch_hash"], "checkpoint": config["checkpoint"]},
                action, reconcile)
        def restore(state):
            if task.get("progress"):
                self.store.assert_owner(task, task["progress"]["run_id"])
                attempt = task["progress"].get("in_flight") or {}
                key = attempt.setdefault("restore_key", uuid4().hex)
                self.store.put(task)
                # Reset is deterministic, but a previous receipt alone does not prove the files still match.
                self.durable_operations.begin(key, state["task_id"], "restore_workspace", {"checkpoint": config["checkpoint"]})
                result = services.restore_checkpoint(state)
                self.durable_operations.finish(key, "completed", {"manifest": digest(file_manifest(workspace, source=True))})
                return result
            return services.restore_checkpoint(state)
        return replace(services,
            apply_patch=lambda state: effect("apply_patch", state, lambda: services.apply_patch(state)),
            save_case=lambda state: effect("save_case", state, lambda: services.save_case(state)),
            record_rejection=lambda state: effect("reject_patch", state, lambda: services.record_rejection(state)),
            restore_checkpoint=restore)

    def run(self, task_id, reviewing, run_id=None):
        with diagnostic_scope(path=self.store.root / "tasks" / task_id / "errors.jsonl", task_id=task_id):
            if self.store.get(task_id).get("progress"):
                return self.run_checkpointed(task_id, reviewing, run_id)
            return self.run_guarded(task_id, reviewing)

    def settle_deadline(self, task, fault):
        """Finish a proven safe timeout; uncertain writes/user-code results retain their checkpoint."""
        progress = task["progress"]
        attempt = progress.get("in_flight")
        if attempt:
            if attempt.get("input_hash") != digest(attempt.get("input_state")):
                return False
            node = attempt["node"]
            safe = node in ("prepare_workspace", "collect_context", "review_patch")
            safe = safe or (node == "propose_patch" and attempt.get("model_tool_policy") == "read_only")
            safe = safe or (node in ("baseline_reproduction", "validate_patch")
                            and attempt.get("code_execution_started") is False)
            if not safe:
                return False
        elif progress.get("next_node") == "finish" and progress.get("committed_state"):
            if digest(progress["committed_state"]) != progress.get("state_hash"):
                return False
            # The result committed before worker shutdown exceeded its deadline.
            fault.outcome_unknown = False
            progress.update(interrupted=False, clock=None)
            return True
        for call in progress["pending_calls"]:
            if not call.get("reservation_charged"):
                for key, reserved in (("tokens", "reserved_tokens"), ("cost", "reserved_cost")):
                    task["state"]["remaining_budget"][key] = max(0, task["state"]["remaining_budget"][key] - call[reserved])
                call["reservation_charged"] = True
        if progress["pending_calls"]:
            task["state"].setdefault("accounting", {}).update(cost_complete=False, usage_complete=False)
        config = task["config"]
        if config.get("checkpoint"):
            with cleanup_scope():
                restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
        fault.outcome_unknown = False
        task["state"].update(failure_update(fault, operation="task.worker"))
        progress.update(interrupted=False, in_flight=None, next_node="finish", clock=None,
                        committed_state=deepcopy(task["state"]), state_hash=digest(task["state"]))
        return True

    def run_checkpointed(self, task_id, reviewing, run_id=None):
        task = self.store.get(task_id)
        run_id = run_id or task["progress"]["run_id"]
        if not run_id:
            task, run_id = self.store.claim(task_id)
            if task is None:
                return
        self.store.assert_owner(task, run_id)
        if self.worker_mode == "inline":
            return self.run_checkpointed_inline(task_id, reviewing, run_id=run_id)
        seconds, started = task["state"]["remaining_budget"]["seconds"], time.monotonic()
        fault, stop_unconfirmed = None, False
        try:
            result = run_process([sys.executable, "-X", "utf8", "-m", "repair_worker",
                                  str(self.store.root), task_id, "review" if reviewing else "repair", run_id],
                                 timeout=max(0.001, seconds), capture_output=True,
                                 on_started=lambda process: self.store.record_worker(task_id, process, run_id))
            if result.returncode:
                fault = Fault("worker_crashed", "任务执行进程异常退出", category="unknown", outcome_unknown=True)
                error_info(fault, operation="task.worker", details=process_evidence(result.stderr, result.returncode))
        except subprocess.TimeoutExpired as error:
            fault = Fault("deadline_exceeded", "自动执行时间预算已耗尽，执行进程已停止", category="budget",
                          outcome_unknown=True, causes=[error_info(error, operation="task.worker.timeout")])
        except Exception as error:
            info = error_info(error, operation="task.worker.supervise")
            stop_unconfirmed = info["code"] == "process_stop_failed"
            fault = error if isinstance(error, Fault) else Fault("worker_start_failed", "无法启动或监管执行进程",
                            category="unknown", outcome_unknown=True, causes=[info])
        if not self.store.owns(task_id, run_id):
            return
        task = self.store.get(task_id)
        if fault and task["config"].get("execution_backend") == "docker" and not stop_unconfirmed:
            if not cleanup_docker_task(task_id):
                stop_unconfirmed = True
                fault = Fault("sandbox_cleanup_failed", "任务进程停止后未能确认容器已清理", category="unknown",
                              outcome_unknown=True, causes=[error_info(fault, operation="task.worker")])
        task["worker_stop_unconfirmed"] = stop_unconfirmed
        task["execution"] = "idle"
        task["state"]["remaining_budget"]["seconds"] = min(task["state"]["remaining_budget"]["seconds"],
                                                          max(0, seconds - (time.monotonic() - started)))
        settled = False
        if fault and fault.code == "deadline_exceeded" and not stop_unconfirmed:
            try:
                settled = self.settle_deadline(task, fault)
            except Exception as error:
                fault = rollback_error(error_info(fault, operation="task.worker"), error)
        if fault and not settled:
            task["progress"]["interrupted"] = True
            task["state"].update(failure_update(fault, operation="task.worker"))
        else:
            task["progress"]["clock"] = None
        self.store.revoke(task)
        if fault:
            trace("task.worker", "stopped", fault=fault, decision="deadline_settled" if settled else "preserve_checkpoint", task_id=task_id,
                  path=self.store.root / "tasks" / task_id / "trace.jsonl")
            trace_workflow_end(task["state"], path=self.store.root / "tasks" / task_id / "trace.jsonl", run_id=run_id)

    def run_guarded(self, task_id, reviewing):
        if self.worker_mode == "inline":
            return self.run_inline(task_id, reviewing)
        task = self.store.get(task_id)
        seconds = task["state"]["remaining_budget"]["seconds"]
        started = time.monotonic()
        worker_token = uuid4().hex
        stop_unconfirmed = False
        # The process deadline bounds workspace preparation and all Python/network execution.
        try:
            result = run_process([sys.executable, "-X", "utf8", "-m", "repair_worker",
                                  str(self.store.root), task_id, "review" if reviewing else "repair", worker_token],
                                 timeout=max(0.001, seconds), capture_output=True,
                                 on_started=lambda process: self.store.record_worker(task_id, process, worker_token))
            if result.returncode == 0:
                task = self.store.get(task_id)
                task["execution"] = "idle"
                self.store.put(task)
                return
            fault = Fault("worker_crashed", "任务执行进程异常退出", category="unknown", outcome_unknown=True,
                          next_action="检查执行记录后人工处理")
            error_info(fault, operation="task.worker", details=process_evidence(result.stderr, result.returncode))
        except subprocess.TimeoutExpired as error:
            fault = Fault("deadline_exceeded", "自动执行时间预算已耗尽，执行进程已停止", category="budget",
                          outcome_unknown=reviewing, next_action="查看执行记录；有副作用时先核对结果",
                          causes=[error_info(error, operation="task.worker.timeout")])
        except Exception as error:
            info = error_info(error, operation="task.worker.supervise")
            stop_unconfirmed = info["code"] == "process_stop_failed"
            fault = error if isinstance(error, Fault) else Fault("worker_start_failed", "无法启动或监管执行进程",
                category="unknown" if reviewing or info["outcome_unknown"] else "internal",
                outcome_unknown=reviewing or info["outcome_unknown"], causes=[info])
        task = self.store.get(task_id)
        if not stop_unconfirmed and task.get("config", {}).get("execution_backend", "host") == "docker":
            if not cleanup_docker_task(task_id):
                stop_unconfirmed = True
                fault = Fault("sandbox_cleanup_failed", "任务进程停止后未能确认容器已清理", category="unknown",
                              outcome_unknown=True, next_action="在 Docker Desktop 中检查任务对应的 repair 容器",
                              causes=[error_info(fault, operation="task.worker")])
        task = self.store.get(task_id)
        task["worker_stop_unconfirmed"] = stop_unconfirmed
        if stop_unconfirmed or not reconcile_review(task, self.store.root):
            task["state"].update(status="needs_input" if fault.outcome_unknown else
                                  "budget_exceeded" if fault.category == "budget" else "failed",
                                  last_error=str(fault), error_info=error_info(fault, operation="task.worker"))
            config = task["config"]
            if config.get("checkpoint") and not reviewing and not stop_unconfirmed:
                try:
                    with cleanup_scope():
                        restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                except Exception as error:
                    task["state"].update(failure_update(rollback_error(task["state"].get("error_info"), error)))
        task["execution"] = "idle"
        task["state"]["remaining_budget"]["seconds"] = max(0, seconds - (time.monotonic() - started))
        self.store.put(task)
        trace("task.worker", "stopped", fault=fault, decision=task["state"]["status"], task_id=task_id,
              path=self.store.root / "tasks" / task_id / "trace.jsonl")
        trace_workflow_end(task["state"], path=self.store.root / "tasks" / task_id / "trace.jsonl")

    def run_inline(self, task_id, reviewing, *, managed=False):
        if self.store.get(task_id).get("progress"):
            return self.run_checkpointed_inline(task_id, reviewing, managed=managed)
        task = self.store.get(task_id)
        task["execution"] = "running"
        self.store.put(task)
        initial_seconds = task["state"]["remaining_budget"]["seconds"]
        initial_elapsed = task["state"]["elapsed_seconds"]
        with execution_scope(initial_seconds, task_id=task_id,
                trace_path=self.store.root / "tasks" / task_id / "trace.jsonl",
                circuit_path=self.store.root / "circuits.sqlite3",
                max_tokens=task["state"]["remaining_budget"]["tokens"],
                max_cost=task["state"]["remaining_budget"]["cost"]) as context:
            prior = task["state"].get("accounting", {})
            context.prior_accounting = prior
            context.cost_known = prior.get("cost_complete", True)
            context.usage_known = prior.get("usage_complete", True)
            context.warnings = list(task["state"].get("warnings", []))
            try:
                if not reviewing:
                    config, source = task["config"], task["state"]["source"]
                    config["checkpoint"] = create_checkpoint_workspace(
                        Path(config["source_root"]), Path(config["workspace"]),
                        revision=str(source["revision"]) if source["type"] == "git_repository" else None,
                        source_file=Path(source["path"]) if source["type"] == "python_file" else None,
                        pasted_code=task["state"]["code_text"])
                    task["state"]["remaining_budget"]["seconds"] = context.remaining()
                    self.store.put(task)
                context.check()
                graph = build_repair_workflow(self.services(task))
                for state in graph.stream(task["state"], config={"recursion_limit": 100}, stream_mode="values"):
                    task["state"] = dict(state)
                    task["state"]["elapsed_seconds"] = initial_elapsed + initial_seconds - context.remaining()
                    self.store.put(task)
            except Exception as error:
                info = error_info(error)
                task["state"].update(status="needs_input" if info["outcome_unknown"] else
                    "budget_exceeded" if info["category"] == "budget" else "failed",
                    last_error=info["message"], error_info=info)
            finally:
                if (not reviewing and task["state"]["status"] not in ("reviewable", "succeeded")
                        and not (task["state"].get("error_info") or {}).get("outcome_unknown")):
                    config = task["config"]
                    if config.get("checkpoint"):
                        try:
                            with cleanup_scope():
                                restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                        except Exception as error:
                            task["state"].update(failure_update(rollback_error(task["state"].get("error_info"), error)))
                task["state"]["remaining_budget"]["seconds"] = context.remaining()
                task["state"]["elapsed_seconds"] = initial_elapsed + initial_seconds - context.remaining()
                task["execution"] = "running" if managed else "idle"
                self.store.put(task)
                if not context.workflow_ended:
                    trace_workflow_end(task["state"])

    def run_checkpointed_inline(self, task_id, reviewing, *, managed=False, run_id=None):
        task = self.store.get(task_id)
        run_id = run_id or task["progress"]["run_id"]
        if not run_id:
            task, run_id = self.store.claim(task_id)
            if task is None:
                return
        self.store.assert_owner(task, run_id)
        task["execution"] = "running"
        self.store.put(task)
        seconds, elapsed = task["state"]["remaining_budget"]["seconds"], task["state"]["elapsed_seconds"]
        with execution_scope(seconds, task_id=task_id, run_id=run_id,
                trace_path=self.store.root / "tasks" / task_id / "trace.jsonl",
                circuit_path=self.store.root / "circuits.sqlite3",
                max_tokens=task["state"]["remaining_budget"]["tokens"],
                max_cost=task["state"]["remaining_budget"]["cost"]) as context:
            prior = task["state"].get("accounting", {})
            context.prior_accounting = prior
            context.cost_known, context.usage_known = prior.get("cost_complete", True), prior.get("usage_complete", True)
            context.warnings = list(task["state"].get("warnings", []))
            context.before_node = lambda node, state: self.store.begin_step(task, node, state, run_id)

            def code_execution():
                self.store.assert_owner(task, run_id)
                task["progress"]["in_flight"]["code_execution_started"] = True
                self.store.put(task)

            context.on_code_execution = code_execution

            def completed(node, state, update):
                self.store.assert_owner(task, run_id)
                task["progress"]["workspace_manifest"] = file_manifest(Path(task["config"]["workspace"]), source=True)
                self.store.finish_step(task, node, state, update, next_node_after(node, {**state, **update}), run_id)

            def request(event, identifier, operation):
                self.store.assert_owner(task, run_id)
                calls = task["progress"]["pending_calls"]
                if event == "started":
                    calls.append({"id": identifier, "operation": operation, "status": "pending",
                                  "reserved_tokens": max(0, context.max_tokens - context.tokens),
                                  "reserved_cost": max(0, context.max_cost - context.cost)})
                elif event == "unknown":
                    for entry in calls:
                        if entry["id"] == identifier:
                            entry["status"] = "unknown"
                else:
                    task["progress"]["pending_calls"] = [entry for entry in calls if entry["id"] != identifier]
                # Charge and pending-call completion must be one task transaction.
                self.store.account(task, context)

            context.after_node, context.on_account, context.on_request = completed, lambda ctx: self.store.account(task, ctx), request
            try:
                self.store.account(task, context)
                if task["progress"]["next_node"] == "prepare_workspace":
                    self.store.begin_step(task, "prepare_workspace", task["state"], run_id)
                    config, source = task["config"], task["state"]["source"]
                    if source_fingerprint(source, Path(config["source_root"])) != config["source_fingerprint"]:
                        raise Fault("source_changed", "输入目录在任务排队期间已变化，不能替换原始输入", category="conflict")
                    config["checkpoint"] = create_checkpoint_workspace(
                        Path(config["source_root"]), Path(config["workspace"]),
                        revision=str(source["revision"]) if source["type"] == "git_repository" else None,
                        source_file=Path(source["path"]) if source["type"] == "python_file" else None,
                        pasted_code=task["state"]["code_text"])
                    if source_fingerprint(source, Path(config["source_root"])) != config["source_fingerprint"]:
                        raise Fault("source_changed", "创建工作区期间输入发生变化", category="conflict")
                    task["progress"]["workspace_manifest"] = file_manifest(Path(config["workspace"]), source=True)
                    task["progress"]["baseline_manifest"] = task["progress"]["workspace_manifest"]
                    self.store.finish_step(task, "prepare_workspace", task["state"], {}, "baseline_reproduction", run_id)
                context.check()
                graph = build_repair_workflow(self.services(task), start_node=task["progress"]["next_node"])
                # Node hooks persist intent/result before LangGraph follows an edge.
                graph.invoke(task["state"], config={"recursion_limit": 100})
            except Exception as error:
                if not self.store.owns(task_id, run_id):
                    raise
                # Preserve uncertain files and in-flight intent for startup recovery.
                saved = self.store.get(task_id)
                task.clear()
                task.update(saved)
                task["progress"]["interrupted"] = True
                task["state"].update(failure_update(error, operation="task.execute"))
            finally:
                if self.store.owns(task_id, run_id):
                    self.store.account(task, context)
                    task["state"]["elapsed_seconds"] = elapsed + seconds - context.remaining()
                    task["state"]["warnings"] = list(context.warnings)
                    task["execution"] = "running" if managed else "idle"
                    if not task["progress"]["interrupted"]:
                        task["progress"]["clock"] = None
                    if managed:
                        self.store.put(task)
                    else:
                        self.store.revoke(task)
                    if not context.workflow_ended:
                        trace_workflow_end(task["state"])

    def review(self, task_id, request: ReviewInput):
        with self.lock:
            task = self.store.get(task_id)
            state = task["state"]
            reason = request.reason.strip()
            if request.decision == "rejected" and not reason:
                raise HTTPException(422, "拒绝补丁必须填写理由")
            if task["review"]:
                previous = task["review"]
                if all(previous[k] == v for k, v in (
                    ("decision", request.decision), ("reason", reason), ("patch_hash", request.patch_hash))):
                    return task
                raise HTTPException(409, "任务已有审核决定")
            validation = state.get("validation") or {}
            actual_hash = hashlib.sha256((state.get("patch_diff") or "").encode("utf-8")).hexdigest()
            if (task["execution"] != "idle" or state["status"] != "reviewable"
                    or validation.get("passed") is not True or state.get("patch_hash") != request.patch_hash
                    or validation.get("patch_hash") != request.patch_hash or actual_hash != request.patch_hash):
                raise HTTPException(409, "补丁尚未通过验证或版本已变化，请刷新任务")
            review = {"decision": request.decision, "reason": reason,
                      "patch_hash": request.patch_hash, "created_at": timestamp()}
            task.update(review=review, execution="queued")
            state.update(review_decision=request.decision, review_reason=reason or None)
            if task.get("progress"):
                task["progress"]["next_node"] = "review_patch"
            self.store.record_review(task, review)
            trace("task.review", "recorded", task_id=task_id,
                  path=self.store.root / "tasks" / task_id / "trace.jsonl", result=review)
            self.submit(task_id, True)
            return self.store.get(task_id)


def create_app(data_root: Path | None = None):
    root = data_root or Path(os.environ.get("REPAIR_DATA_DIR", Path(__file__).parent / "work" / "web"))
    with diagnostic_scope(path=root / "errors.jsonl", operation="api.initialize"):
        try:
            runner = RepairRunner(root)
        except Exception as error:
            error_info(error, operation="api.initialize")
            raise

    @asynccontextmanager
    async def lifespan(app):
        runner.service_lock.acquire()
        try:
            with diagnostic_scope(path=root / "errors.jsonl", operation="api.startup"):
                try:
                    runner.store.recover_interrupted()
                    from recovery_manager import RecoveryManager
                    RecoveryManager(runner).recover_pending()
                    runner.start_dispatcher()
                except Exception as error:
                    error_info(error, operation="api.startup")
                    raise
            yield
        finally:
            with diagnostic_scope(path=root / "errors.jsonl", operation="api.shutdown"):
                try:
                    runner.dispatch_stop.set()
                    if runner.dispatch_thread:
                        runner.dispatch_thread.join(timeout=5)
                    runner.pool.shutdown(wait=True)
                except Exception as error:
                    error_info(error, operation="api.shutdown")
                    raise
                finally:
                    runner.service_lock.release()

    app = FastAPI(title="Code Repair Workbench", lifespan=lifespan)
    app.state.runner = runner

    @app.exception_handler(Fault)
    async def fault_response(request, error):
        status = 409 if error.outcome_unknown else {"transient": 503, "storage": 503, "permission": 403,
            "business": 409, "validation": 422, "timeout": 504, "budget": 408}.get(error.category, 500)
        headers = {"Retry-After": str(max(0, int(error.retry_after)))} if error.retry_after is not None else None
        info = error_info(error, operation="http.request")
        return JSONResponse({"detail": info["message"], "error": info}, status_code=status, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_response(request, error):
        fault = Fault(f"http_{error.status_code}", str(error.detail),
            category="permission" if error.status_code in (401, 403) else "validation" if error.status_code == 422 else "business")
        info = record_error(error, fault.as_dict(), operation="http.request")
        return JSONResponse({"detail": info["message"], "error": info}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(request, error):
        details = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in error.errors()]
        info = record_error(error, Fault("invalid_arguments", "请求参数校验失败", category="validation").as_dict(),
                            operation="http.validation", details={"issue_types": [item["type"] for item in details]})
        return JSONResponse({"detail": details, "error": info}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error_response(request, error):
        info = error_info(error)
        return JSONResponse({"detail": info["message"], "error": info}, status_code=500)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        request_id = uuid4().hex
        match = re.fullmatch(r"/api/tasks/([a-f0-9]{32})(?:/[^/]+)?", request.url.path)
        task_id = match[1] if match else None
        path = root / "tasks" / task_id / "errors.jsonl" if task_id else root / "errors.jsonl"
        with diagnostic_scope(path=path, task_id=task_id, request_id=request_id, operation=f"http.{request.method}"):
            try:
                if request.headers.get("host", "").split(":")[0] not in ("127.0.0.1", "localhost", "testserver"):
                    raise Fault("local_only", "仅支持本地服务", category="permission")
                origin = request.headers.get("origin")
                if origin and urlsplit(origin).netloc not in ("127.0.0.1:5173", "localhost:5173", request.headers.get("host")):
                    raise Fault("origin_denied", "请求来源不受支持", category="permission")
                response = await call_next(request)
            except Fault as error:
                response = await fault_response(request, error)
            except Exception as error:
                response = await unexpected_error_response(request, error)
            response.headers["X-Request-ID"] = request_id
            return response

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/client-errors", status_code=202)
    def client_error(request: ClientFaultInput):
        fault = Fault(f"client_{request.code}", "浏览器报告执行或连接故障", category="frontend",
                      next_action="检查浏览器状态及对应请求诊断")
        fault.error_id = request.error_id
        path = root / "tasks" / request.task_id / "errors.jsonl" if request.task_id else root / "errors.jsonl"
        with diagnostic_scope(path=path, task_id=request.task_id, operation=f"frontend.{request.code}"):
            info = error_info(fault)
        return {"error_id": info["error_id"], "recorded": info["diagnostics_persisted"]}

    @app.post("/api/tasks", status_code=202)
    def create(request: TaskInput, http_request: Request):
        return runner.create(request, http_request.headers.get("Idempotency-Key"))

    @app.get("/api/requests/{key}")
    def request_status(key: str):
        task = runner.store.request_task(key)
        if task is None:
            raise HTTPException(404, "尚无该幂等键对应的任务")
        return task

    @app.get("/api/tasks/{task_id}/trace")
    def task_trace(task_id: str, cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        runner.store.get(task_id)
        path = runner.store.root / "tasks" / task_id / "trace.jsonl"
        try:
            return read_trace_page(path, cursor=cursor, limit=limit)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/tasks/{task_id}/errors")
    def task_errors(task_id: str):
        runner.store.get(task_id)
        path = runner.store.root / "tasks" / task_id / "errors.jsonl"
        if not path.exists():
            return {"errors": []}
        from collections import deque
        with path.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=200)
        records, skipped = [], 0
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError as error:
                skipped += 1
                error_info(error, operation="diagnostics.read")
        return {"errors": records, "skipped_invalid_records": skipped}

    @app.get("/api/tasks/{task_id}")
    def get(task_id: str):
        return runner.store.get(task_id)

    @app.post("/api/tasks/{task_id}/review", status_code=202)
    def review(task_id: str, request: ReviewInput):
        return runner.review(task_id, request)

    @app.get("/api/knowledge/stats")
    def knowledge_stats():
        return {"total": runner.knowledge.count()}

    @app.post("/api/knowledge/import")
    def knowledge_import(request: KnowledgeImportInput):
        with execution_scope(120, task_id="knowledge-import", circuit_path=root / "circuits.sqlite3",
                             trace_path=root / "trace.jsonl"):
            return runner.knowledge.import_cases(request.cases, "http-import")

    @app.post("/api/knowledge/search")
    def knowledge_search(request: KnowledgeSearchInput):
        try:
            with execution_scope(120, task_id="knowledge-search", circuit_path=root / "circuits.sqlite3",
                                 trace_path=root / "trace.jsonl") as context:
                matches = runner.knowledge.search(request.query, request.limit, request.min_similarity)
                return {"matches": matches, "warnings": context.warnings}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    dist = Path(__file__).parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
