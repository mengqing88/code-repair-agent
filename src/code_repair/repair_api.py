"""Local HTTP adapter for LangGraph; durable snapshots and human review.

Run a single worker with uvicorn code_repair.repair_api:app --host 127.0.0.1 --port 8000.
Source code and pytest run in the constrained Docker sandbox by default.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from code_repair.paths import PROJECT_ROOT
import re
import shutil
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
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
import hmac
from code_repair.user_accounts import UserAccounts, SESSION_COOKIE, SESSION_SECONDS, csrf_token
from code_repair.personal_memory import PersonalMemory, MemoryInput, memory_scope
from code_repair.conversation_memory import Conversations, ChatInput, CandidateInput, conversation_lock
from code_repair.task_lifecycle import transition_scope


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
_LOCAL_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


def _parse_authority(authority: str) -> tuple[str, int | None] | None:
    if not authority or authority != authority.strip() or any(char in authority for char in "/\\?#@"):
        return None
    try:
        parsed = urlsplit("//" + authority)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (not hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password
            or parsed.netloc != authority or authority.endswith(":")):
        return None
    return hostname.rstrip(".").lower(), port


def _public_web_allowlist() -> tuple[set[str], set[str]]:
    hosts = {value.strip().rstrip(".").lower()
             for value in os.environ.get("REPAIR_ALLOWED_HOSTS", "").split(",") if value.strip()}
    origins = {value.strip().rstrip("/").lower()
               for value in os.environ.get("REPAIR_ALLOWED_ORIGINS", "").split(",") if value.strip()}
    if not hosts and not origins:
        return hosts, origins
    if os.environ.get("REPAIR_DEPLOYMENT_MODE", "local").strip().lower() != "untrusted":
        raise ValueError("公网 Host/Origin 白名单只能与 REPAIR_DEPLOYMENT_MODE=untrusted 一起使用")
    for host in hosts:
        labels = host.split(".")
        if (len(host) > 253 or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                                   for label in labels)):
            raise ValueError("REPAIR_ALLOWED_HOSTS 必须是精确域名，不能使用通配符、端口或 URL")
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in hosts
                or parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment
                or parsed.netloc != parsed.hostname):
            raise ValueError("REPAIR_ALLOWED_ORIGINS 必须是白名单域名对应的 HTTPS Origin")
    return hosts, origins


def _allowed_request_host(authority: str, public_hosts: set[str]) -> bool:
    parsed = _parse_authority(authority)
    if parsed is None:
        return False
    hostname, port = parsed
    if hostname in _LOCAL_HOSTS:
        return True
    return hostname in public_hosts and port in (None, 443)


def _allowed_request_origin(origin: str, authority: str, public_origins: set[str]) -> bool:
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return False
        origin_authority = _parse_authority(parsed.netloc)
        request_authority = _parse_authority(authority)
        if origin_authority is None or request_authority is None:
            return False
        origin_hostname, origin_port = origin_authority
        request_hostname, request_port = request_authority
        normalized = f"{parsed.scheme}://{origin_hostname}"
        if origin_port is not None:
            normalized += f":{origin_port}"
        if request_hostname in _LOCAL_HOSTS and normalized in _LOCAL_ORIGINS:
            return True
        same_origin_port = origin_port or (443 if parsed.scheme == "https" else 80)
        request_default_port = request_port or (443 if parsed.scheme == "https" else 80)
        same_origin = origin_hostname == request_hostname and same_origin_port == request_default_port
        if request_hostname in _LOCAL_HOSTS and same_origin:
            return True
        return (normalized in public_origins and origin_hostname == request_hostname
                and same_origin_port == 443 and request_port in (None, 443))
    except ValueError:
        return False

from code_repair.agent_cli import check_source
from code_repair.knowledge_base import KnowledgeBase
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, create_checkpoint_workspace, restore_checkpoint_workspace
from code_repair.repair_workflow import build_repair_workflow, next_node_after
from code_repair.recovery_store import (ProgressStore, initial_progress, setup_recovery, ServiceLock,
                            source_fingerprint, file_manifest, boot_identity)
from code_repair.reproduction import reproduce
from code_repair.operation_store import OperationStore
from code_repair.docker_executor import cleanup_docker_task, get_execution_backend
from code_repair.resilience import (Fault, digest, error_info, trace, execution_scope, current,
                        cleanup_scope, run_process, process_identity, stop_recorded_worker, rollback_error, failure_update,
                        trace_workflow_end, traced_call)
from code_repair.trace_store import read_trace_page
from code_repair.diagnostics import diagnostic_scope, process_evidence, record_error, redact
from code_repair.execution_manifest import (SnapshotKnowledge, freeze as freeze_execution_manifest,
                                task_configuration, verify as verify_execution_manifest)
from code_repair.business_scope import require_software_request
from code_repair.language_support import (detect_language, detect_project_language, normalize_language, split_command,
                              validate_execution_commands)
from code_repair.project_inputs import (MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_PROJECT_FILES, MAX_TASK_REQUEST_BYTES, decode_base64,
                            import_local_directory, import_uploaded_files, import_zip, project_path)


class ProjectFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    content_base64: str = Field(max_length=4 * ((MAX_FILE_BYTES + 2) // 3))

    @model_validator(mode="after")
    def validate_path(self):
        project_path(self.path)
        return self


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["code_file", "python_file", "git_repository", "pasted_code", "directory", "zip_archive"]
    path: str = Field(default="", max_length=4096)
    revision: str = Field(default="", max_length=64)
    code_text: str = Field(default="", max_length=1_000_000)
    file_name: str = Field(default="", max_length=255)
    file_content: str | None = Field(default=None, max_length=1_000_000)
    project_files: list[ProjectFileInput] | None = Field(default=None, max_length=MAX_PROJECT_FILES)
    archive_base64: str | None = Field(default=None, max_length=4 * ((MAX_ARCHIVE_BYTES + 2) // 3))
    error_report: str = Field(min_length=1, max_length=100_000)
    test_target: str = Field(default="", max_length=4096)
    language: str = Field(default="auto", max_length=40)
    test_command: str = Field(default="", max_length=4096)
    reproduce_command: str = Field(default="", max_length=4096)
    regression_command: str = Field(default="", max_length=4096)
    runtime_seconds: float = Field(default=1200.0, gt=0, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_input(self):
        if not self.error_report.strip():
            raise ValueError("错误报告不能为空")
        if self.source_type == "pasted_code" and not self.code_text.strip():
            raise ValueError("代码不能为空")
        if (self.source_type != "pasted_code" and not self.path.strip() and self.file_content is None
                and self.project_files is None and self.archive_base64 is None):
            raise ValueError("请提供本地路径或上传文件")
        if self.project_files is not None and (self.source_type != "directory" or not self.project_files):
            raise ValueError("文件夹上传必须包含项目文件")
        if self.archive_base64 is not None and self.source_type != "zip_archive":
            raise ValueError("压缩包数据仅适用于 ZIP 模式")
        if self.source_type == "zip_archive":
            if self.archive_base64 is None or not self.file_name.lower().endswith(".zip"):
                raise ValueError("请上传 ZIP 压缩包")
        if self.project_files is not None and self.archive_base64 is not None:
            raise ValueError("不能同时上传文件夹和压缩包")
        if self.file_content is not None:
            name = self.file_name
            if self.source_type not in ("code_file", "python_file") or not self.file_content.strip():
                raise ValueError("只有文件模式可上传非空文件")
            if not name or name in (".", "..") or any(c in name for c in '/\\:'):
                raise ValueError("上传文件名必须是普通文件名")
            if name.startswith(".") or name.endswith((".", " ")):
                raise ValueError("上传文件名不能以点开头或以点、空格结尾")
            if Path(name).is_reserved():
                raise ValueError("不能使用系统保留文件名")
        if self.source_type != "git_repository" and self.revision:
            raise ValueError("固定版本仅适用于 Git 仓库")
        try:
            split_command(self.test_command or None)
            split_command(self.reproduce_command or None)
            split_command(self.regression_command or None)
        except ValueError as error:
            raise ValueError(f"执行命令配置无效：{error}") from error
        self.language = normalize_language(self.language)
        if self.language == "auto":
            if self.source_type == "python_file":
                self.language = "python"
            elif self.source_type == "code_file":
                self.language = detect_language(path=self.file_name or self.path)
            elif self.source_type == "git_repository" and self.path.strip():
                self.language = detect_project_language(self.path)
            elif self.source_type == "pasted_code":
                self.language = detect_language(path=self.file_name or None, content=self.code_text)
        if self.language != "auto" or self.source_type not in ("directory", "zip_archive"):
            try:
                validate_execution_commands(self.language, self.test_command, self.reproduce_command, self.regression_command)
            except ValueError as error:
                raise ValueError(f"{error}；请在任务设置中补充执行命令") from error
        return self

    def persisted_input(self):
        result = self.model_dump(exclude={"project_files", "archive_base64"})
        if self.project_files is not None:
            result["project_files"] = [{"path": item.path,
                "sha256": hashlib.sha256(decode_base64(item.content_base64, MAX_FILE_BYTES)).hexdigest()}
                for item in self.project_files]
        if self.archive_base64 is not None:
            result["archive_sha256"] = hashlib.sha256(decode_base64(self.archive_base64, MAX_ARCHIVE_BYTES)).hexdigest()
        return result


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=2000)
    patch_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClarificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=4000)


class DeadLetterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["requeue", "dismiss"]
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    reason: str = Field(min_length=1, max_length=2000)


class BudgetExtensionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    seconds: float = Field(default=0.0, ge=0, le=3600)
    tokens: int = Field(default=0, ge=0, le=200_000)
    cost: float = Field(default=0.0, ge=0, le=100)
    attempts: int = Field(default=0, ge=0, le=20)
    reason: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class ToolOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["completed", "not_applied"]
    evidence: str = Field(min_length=10, max_length=2000)


class ToolAuthorizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approved", "rejected", "renew"]
    reason: str = Field(default="", max_length=2000)


class KnowledgeImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[dict] = Field(min_length=1, max_length=100)


class KnowledgeReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "decline"]
    reason: str = Field(min_length=1, max_length=2000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=20_000)
    limit: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.35, ge=0, le=1)
    outcome: Literal["accepted", "rejected"] = "accepted"
    repository_id: str | None = Field(default=None, min_length=1, max_length=128)
    file_paths: list[str] = Field(default_factory=list, max_length=10)
    dependency_versions: dict[str, str] = Field(default_factory=dict, max_length=100)


class ClientFaultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["component_error", "browser_error", "unhandled_rejection", "resource_error", "storage_error",
                  "request_timeout", "connection_failed", "response_invalid"]
    error_id: str = Field(pattern=r"^[a-z0-9-]{8,64}$")
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: SecretStr = Field(min_length=1, max_length=128)


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
            from code_repair.dead_letters import setup as setup_dead_letters
            setup_dead_letters(db)
            db.execute("""CREATE TABLE IF NOT EXISTS budget_extensions
                (task_id TEXT NOT NULL, request_id TEXT NOT NULL, parameters TEXT NOT NULL,
                 actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id,request_id))""")
        self.accounts = UserAccounts(self.root)

    def get_owned(self, task_id, user_id):
        # Check ownership before loading a payload or writing task diagnostics.
        owner = self.accounts.owner(task_id)
        if owner != user_id:
            # A missing owner must still distinguish a missing task from a storage outage;
            # no payload is returned and a foreign owned task never reaches this branch.
            if owner is None:
                try:
                    self.get(task_id)
                except HTTPException:
                    pass
            raise HTTPException(404, "任务不存在")
        return self.get(task_id)

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
        from code_repair.task_lifecycle import validate_transition
        validate_transition(previous, task)
        frozen_hash = (previous or {}).get("config", {}).get("execution_manifest_hash")
        if frozen_hash and (task["config"].get("execution_manifest_hash") != frozen_hash
                            or task["config"].get("execution_manifest_path") != "execution-manifest.json"):
            raise Fault("manifest_changed", "不能替换任务已冻结的执行清单", category="validation")
        progress = task.get("progress")
        old = previous.get("progress") if previous else None
        if old:
            owner = progress.get("run_id") if progress and expected_run is _SAME_RUN else expected_run
            if not progress or old["revision"] != progress["revision"] or old["run_id"] != owner:
                raise Fault("stale_execution", "任务版本或执行身份已变化，拒绝旧数据覆盖", category="conflict")
            epoch = progress.get("execution_epoch", 0)
            acquiring = owner is None and progress.get("run_id") is not None
            if acquiring:
                high = db.execute("SELECT value FROM execution_epochs WHERE task_id=?", (task_id,)).fetchone()
                valid_epoch = high is not None and epoch == high[0] and epoch > old.get("execution_epoch", 0)
            else:
                valid_epoch = epoch == old.get("execution_epoch", 0)
            if not valid_epoch:
                raise Fault("stale_execution", "执行纪元不匹配，拒绝过期写入", category="conflict")
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
        from code_repair.dead_letters import capture
        capture(db, previous, task)

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

    def request_task(self, key, parameters=None, user_id=None):
        with self.connect() as db:
            row = db.execute("SELECT params_hash, task_id FROM requests WHERE key=?", (f"{user_id}:{key}" if user_id else key,)).fetchone()
            if row is None and user_id == self.accounts.legacy_owner():
                legacy = db.execute("SELECT params_hash, task_id FROM requests WHERE key=?", (key,)).fetchone()
                if legacy and self.accounts.owner(legacy[1]) == user_id:
                    row = legacy
        if not row:
            return None
        if parameters is not None and row[0] != digest(parameters):
            raise HTTPException(409, "幂等键已用于不同任务参数")
        return self.get_owned(row[1], user_id) if user_id else self.get(row[1])

    def create_once(self, task, key, parameters, user_id=None):
        with self.connect() as db:
            db.execute("INSERT INTO requests VALUES (?, ?, ?)", (f"{user_id}:{key}" if user_id else key, digest(parameters), task["state"]["task_id"]))
            self._write(db, task)
            if user_id:
                db.execute("INSERT INTO task_owners VALUES (?,?)", (task["state"]["task_id"], user_id))

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
        self.max_workers = int(os.environ.get("REPAIR_MAX_WORKERS", "2"))
        if not 1 <= self.max_workers <= 8:
            raise ValueError("REPAIR_MAX_WORKERS 必须在 1 到 8 之间")
        self.pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="repair")
        self.knowledge = KnowledgeBase(self.store.root / "knowledge.sqlite3")
        self.operations = OperationStore(self.store.root / "operations.sqlite3")
        self.durable_operations = OperationStore(self.store.path)
        self.execution_backend = get_execution_backend()
        self.deployment_mode = os.environ.get("REPAIR_DEPLOYMENT_MODE", "local").strip().lower()
        if self.deployment_mode not in ("local", "untrusted"):
            raise ValueError("REPAIR_DEPLOYMENT_MODE 必须是 local 或 untrusted")
        if self.deployment_mode == "untrusted" and self.execution_backend != "docker":
            raise ValueError("不可信用户模式必须使用 Docker 执行")
        self.worker_mode = "process"
        self.dispatch_stop = Event()
        self.dispatch_thread = None
        self.submitted = set()
        self.submit_lock = Lock()
        self.service_lock = ServiceLock(self.store.root)
        self.accounts = self.store.accounts

    def knowledge_for_user(self, user_id):
        return KnowledgeBase(
            self.accounts.knowledge_path(user_id),
            embedder=self.knowledge.embedder,
        )

    @transition_scope("budget_extension")
    def extend_budget(self, task_id, value, principal):
        parameters = value.model_dump()
        if not value.reason.strip() or not any(parameters[k] > 0 for k in ("seconds", "tokens", "cost", "attempts")):
            raise HTTPException(422, "追加预算需要正数额度及理由")
        with self.lock:
            task = self.store.get_owned(task_id, principal)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT parameters FROM budget_extensions WHERE task_id=? AND request_id=?",
                                      (task_id, value.request_id)).fetchone()
                if previous:
                    if json.loads(previous[0]) != parameters:
                        raise HTTPException(409, "相同追加请求不能更换额度或理由")
                else:
                    self.validate_task_identity(task)
                    state, progress = task["state"], task.get("progress")
                    if ((state["status"] != "budget_exceeded" and not
                         (state["status"] == "failed" and state.get("budget_exhausted") == "repair_attempts"))
                            or task["execution"] != "idle"
                            or not progress or progress.get("run_id") or progress.get("in_flight")
                            or progress.get("interrupted") or progress.get("pending_calls")
                            or task.get("worker_stop_unconfirmed") or task.get("review")
                            or (state.get("error_info") or {}).get("outcome_unknown")
                            or self.operations.pending_for_task(task_id)):
                        raise HTTPException(409, "任务存在运行中或未核实操作，不能直接追加续跑")
                    remaining = {k: state["remaining_budget"][k] + parameters[k]
                                 for k in ("seconds", "tokens", "cost", "attempts")}
                    if any(remaining[k] <= 0 for k in ("seconds", "tokens", "cost")):
                        raise HTTPException(422, "请补足已耗尽的时间、Token 和费用预算")
                    if state.get("budget_exhausted") == "repair_attempts" and remaining["attempts"] <= 0:
                        raise HTTPException(422, "请追加修复尝试次数")
                    config = task["config"]
                    if config.get("checkpoint"):
                        with cleanup_scope():
                            restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                        progress["workspace_manifest"] = file_manifest(Path(config["workspace"]), source=True)
                    node = ("prepare_workspace" if not config.get("checkpoint") else
                            "baseline_reproduction" if not state.get("reproduction") else
                            "collect_context" if not state.get("code_evidence") else
                            "retrieve_experience" if state.get("experience_retrieval_pending") else "propose_patch")
                    state.update(remaining_budget=remaining, status="queued", error_info=None,
                                 last_error=None, budget_exhausted=None, question=None, patch_diff=None,
                                 patch_hash=None, validation=None, adversarial_review=None)
                    progress.update(next_node=node, in_flight=None, committed_state=deepcopy(state), state_hash=digest(state))
                    task["execution"] = "queued"
                    db.execute("INSERT INTO budget_extensions VALUES (?,?,?,?,?)",
                        (task_id, value.request_id, json.dumps(parameters, ensure_ascii=False), principal, timestamp()))
                    self.store._write(db, task)
        trace("task.budget_extension", "authorized", task_id=task_id,
              path=self.store.root / "tasks" / task_id / "trace.jsonl",
              details={"actor": principal, "request_id": value.request_id, "duplicate": bool(previous),
                       "grant": {k: parameters[k] for k in ("seconds", "tokens", "cost", "attempts")}})
        self.submit(task_id, False)
        return self.store.get(task_id)

    @transition_scope("dead_letter_requeue")
    def decide_dead_letter(self, identifier, value, principal):
        parameters = value.model_dump()
        parameters["reason"] = redact(value.reason.strip(), limit=2000)
        if not parameters["reason"]:
            raise HTTPException(422, "处理失败任务必须填写原因")
        duplicate, prepared = False, False
        with self.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT task_id,status FROM task_dead_letters WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, "失败记录不存在")
            task_id = row[0]
            task = self.store.get_owned(task_id, principal)
            saved = db.execute("SELECT parameters FROM dead_letter_decisions WHERE id=? AND request_id=?",
                               (identifier, value.request_id)).fetchone()
            if saved:
                if json.loads(saved[0]) != parameters:
                    raise HTTPException(409, "相同处理请求不能更换决定或原因")
                duplicate = True
            else:
                if row[1] != "open":
                    raise HTTPException(409, "失败记录已处理")
                if value.decision == "requeue":
                    self.validate_task_identity(task)
                    if task["config"].get("execution_manifest_path"):
                        verify_execution_manifest(task, self.store.root / "tasks" / task_id, runtime=True)
                    state, progress = task["state"], task.get("progress")
                    if (not progress or task["execution"] != "idle" or progress.get("run_id")
                            or progress.get("in_flight") or progress.get("interrupted") or progress.get("pending_calls")
                            or task.get("worker_stop_unconfirmed") or task.get("review")
                            or (state.get("error_info") or {}).get("outcome_unknown")
                            or self.operations.pending_for_task(task_id)
                            or state["status"] not in {"failed", "timed_out", "needs_input"}):
                        raise HTTPException(409, "任务仍有在途执行或未知结果，必须先核对，不能直接重投")
                    count = db.execute("SELECT COUNT(*) FROM task_dead_letters WHERE task_id=? AND status='requeued'", (task_id,)).fetchone()[0]
                    if count >= 3 or any(state["remaining_budget"][k] <= 0 for k in ("seconds", "tokens", "cost", "attempts")):
                        raise HTTPException(409, "重投上限或原任务预算已耗尽；不会自动补充预算")
                    trace("task.dead_letter", "restore_intent", task_id=task_id,
                          path=self.store.root / "tasks" / task_id / "trace.jsonl", required=True,
                          details={"id": identifier, "actor": principal, "request_id": value.request_id})
                    # Commit intent before touching files; a crash cannot leave an invisible reset.
                    state["remaining_budget"]["attempts"] -= 1
                    state.update(failure_update(Fault("dead_letter_restore_in_progress", "正在恢复重投基线，禁止并发续跑",
                                                    category="unknown", outcome_unknown=True)))
                    progress.update(interrupted=True, clock={"boot": boot_identity(), "tick": time.monotonic(),
                                                            "remaining": state["remaining_budget"]["seconds"]})
                    task["redelivery_restore"] = {"id": identifier, "request_id": value.request_id}
                    task["worker"] = None
                    self.store._write(db, task)
                    db.execute("UPDATE task_dead_letters SET status='preparing' WHERE id=?", (identifier,))
                    prepared = True
                else:
                    db.execute("UPDATE task_dead_letters SET status='dismissed',reason=?,actor=? WHERE id=?",
                               (parameters["reason"], principal, identifier))
                    db.execute("INSERT INTO dead_letter_decisions VALUES (?,?,?)",
                               (identifier, value.request_id, json.dumps(parameters, ensure_ascii=False)))
        if prepared:
            self._finish_dead_letter_restore(task, identifier, value, parameters, principal)
        trace("task.dead_letter", value.decision, task_id=task_id,
              path=self.store.root / "tasks" / task_id / "trace.jsonl",
              details={"id": identifier, "actor": principal, "request_id": value.request_id, "duplicate": duplicate})
        if value.decision == "requeue" and self.store.get(task_id)["execution"] == "queued":
            self.submit(task_id, False)
        return self.store.get_owned(task_id, principal)

    def _finish_dead_letter_restore(self, task, identifier, value, parameters, principal):
        state, progress, config = task["state"], task["progress"], task["config"]
        started = progress["clock"]["tick"]
        failure = None
        try:
            if config.get("checkpoint"):
                with execution_scope(state["remaining_budget"]["seconds"]), cleanup_scope():
                    restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                actual = file_manifest(Path(config["workspace"]), source=True)
                if actual != progress.get("baseline_manifest"):
                    raise Fault("baseline_conflict", "重投前的工作区未恢复到可信基线", category="conflict")
                progress["workspace_manifest"] = actual
        except BaseException as error:
            failure = error
        elapsed = max(0.0, time.monotonic() - started)
        state["remaining_budget"]["seconds"] = max(0.0, state["remaining_budget"]["seconds"] - elapsed)
        state["elapsed_seconds"] += elapsed
        progress["clock"] = None
        if failure is not None:
            fault = Fault("dead_letter_restore_failed", "重投恢复失败，保留意图并暂停；请核对任务副本后人工处理",
                          category="unknown", outcome_unknown=True,
                          causes=[error_info(failure, operation="dead_letter.restore")])
            state.update(failure_update(fault))
            task["execution"] = "idle"
        else:
            node = ("prepare_workspace" if not config.get("checkpoint") else
                    "baseline_reproduction" if not state.get("reproduction") else
                    "collect_context" if not state.get("code_evidence") else
                    "retrieve_experience" if state.get("experience_retrieval_pending") else "propose_patch")
            if state.get("awaiting_node") == "propose_patch":
                state.setdefault("clarifications", []).append(parameters["reason"])
            state.update(status="queued", error_info=None, last_error=None, question=None, awaiting_node=None,
                         patch_diff=None, patch_hash=None, validation=None, adversarial_review=None)
            if state["remaining_budget"]["seconds"] <= 0:
                state.update(failure_update(Fault("deadline_exceeded", "重投恢复已耗尽任务时间预算", category="budget", budget_kind="runtime")))
            progress.update(next_node=node, interrupted=False, committed_state=deepcopy(state), state_hash=digest(state))
            task["execution"] = "queued" if state["status"] == "queued" else "idle"
            task["redelivery_restore"] = None
        with self.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store._write(db, task)
            db.execute("UPDATE task_dead_letters SET status=?,reason=?,actor=? WHERE id=?",
                       ("requeue_failed" if failure else "requeued", parameters["reason"], principal, identifier))
            db.execute("INSERT INTO dead_letter_decisions VALUES (?,?,?)",
                       (identifier, value.request_id, json.dumps(parameters, ensure_ascii=False)))
        if failure is not None:
            if not isinstance(failure, Exception):
                raise failure
            raise fault from failure

    def personal_memory(self, user_id):
        self.accounts.knowledge_path(user_id)  # Validate the authoritative user identity before building paths.
        return PersonalMemory(self.store.root / "users" / user_id / "personal_memory.sqlite3")

    def knowledge_reviewer(self, user_id):
        from code_repair.knowledge_review import KnowledgeReview
        def resolve_task(task_id):
            task = self.store.get_owned(task_id, user_id)
            with self.store.connect() as db:
                row = db.execute("SELECT decision,reason,patch_hash,created_at FROM repair_reviews WHERE task_id=?",
                                 (task_id,)).fetchone()
            recorded = dict(zip(("decision", "reason", "patch_hash", "created_at"), row)) if row else None
            if recorded != task.get("review"):
                return None
            return task
        return KnowledgeReview(self.knowledge_for_user(user_id), resolve_task=resolve_task)

    def clarify(self, task_id, answer):
        answer = redact(answer.strip(), limit=4000)
        if not answer:
            raise HTTPException(422, "补充说明不能为空")
        with self.lock:
            task = self.store.get(task_id)
            state, progress = task["state"], task.get("progress")
            if (not progress or task["execution"] != "idle" or state["status"] != "needs_input"
                    or (state.get("error_info") or {}).get("outcome_unknown")
                    or state.get("awaiting_node") not in ("baseline_reproduction", "collect_context", "propose_patch")):
                raise HTTPException(409, "当前暂停原因不能通过补充说明继续，请先处理错误记录")
            node = state["awaiting_node"]
            state.setdefault("clarifications", []).append(answer)
            state.update(status="queued", question=None, last_error=None, error_info=None, awaiting_node=None)
            if node == "baseline_reproduction":
                state.update(reproduction=None, code_evidence=[], case_matches=[], failure_matches=[])
            task["execution"] = "queued"
            progress.update(next_node=node, in_flight=None, interrupted=False, committed_state=deepcopy(state),
                            state_hash=digest(state))
            self.store.put(task)
        trace("task.clarification", "accepted", task_id=task_id, details={"node": node})
        self.submit(task_id, False)
        return self.store.get(task_id)

    @transition_scope("tool_outcome")
    def resolve_tool_outcome(self, task_id, request):
        evidence = redact(request.evidence.strip(), limit=2000)
        if len(evidence) < 10:
            raise HTTPException(422, "请说明你核对了哪个外部记录")
        with self.lock:
            task = self.store.get(task_id)
            state, progress = task["state"], task.get("progress")
            if not progress or task["execution"] != "idle" or state["status"] != "needs_input":
                raise HTTPException(409, "任务当前没有待核对的外部操作")
            pending = {item["operation_id"] for item in self.operations.pending_for_task(task_id)}
            if request.operation_id not in pending:
                raise HTTPException(404, "待核对外部操作不存在或已处理")
            self.operations.resolve_unknown(request.operation_id, task_id, request.outcome, evidence)
            resolution = {"operation_id": request.operation_id, "outcome": request.outcome,
                          "evidence": evidence, "created_at": timestamp()}
            state.setdefault("tool_resolutions", []).append(resolution)
            remaining = self.operations.pending_for_task(task_id)
            completed = any(r.get("outcome") == "completed" for r in state["tool_resolutions"])
            if remaining:
                fault = Fault("mcp_outcome_unknown", "仍有其他外部操作未核实，继续暂停",
                              category="unknown", outcome_unknown=True, operation_id=remaining[0]["operation_id"])
                state.update(failure_update(fault))
            elif not completed:
                state.update(status="queued", question=None, last_error=None, error_info=None, awaiting_node=None)
                progress.update(next_node="propose_patch", in_flight=None, interrupted=False,
                                committed_state=deepcopy(state), state_hash=digest(state))
                task["execution"] = "queued"
            else:
                fault = Fault("external_operation_confirmed", "外部操作已核实为完成，但没有可用返回值；修复流程已停止",
                              category="business", next_action="检查外部服务记录；如仍需修复，请新建任务")
                state.update(status="failed", question=None, last_error=str(fault), error_info=error_info(fault))
                progress.update(next_node="finish", in_flight=None, interrupted=False,
                                committed_state=deepcopy(state), state_hash=digest(state))
                task["execution"] = "idle"
            self.store.put(task)
        trace("mcp.operation", "manually_resolved", task_id=task_id,
              details={"operation_id": request.operation_id, "outcome": request.outcome})
        if task["execution"] == "queued":
            self.submit(task_id, False)
        return self.store.get(task_id)

    def task_with_pending_operations(self, task):
        from code_repair.tool_authorization import ToolAuthorizations
        result = deepcopy(task)
        result["pending_external_operations"] = self.operations.pending_for_task(task["state"]["task_id"])
        principal = self.accounts.owner(task["state"]["task_id"]) or task["config"].get("principal", "local-user")
        result["pending_tool_authorizations"] = (
            [item for item in ToolAuthorizations(self.operations).pending(task["state"]["task_id"], principal)
             if item["authorization_id"] == (task["state"].get("error_info") or {}).get("operation_id")]
            if task["state"].get("awaiting_node") == "tool_authorization" else [])
        return result

    def authorize_tool(self, task_id, request, principal):
        from code_repair.tool_authorization import ToolAuthorizations
        with self.lock:
            task = self.store.get_owned(task_id, principal)
            self.validate_task_identity(task)
            state, progress = task["state"], task.get("progress")
            if (not progress or task["execution"] != "idle" or state["status"] != "needs_input"
                    or state.get("awaiting_node") != "tool_authorization"):
                raise HTTPException(409, "任务当前没有待授权的操作")
            if (state.get("error_info") or {}).get("operation_id") != request.authorization_id:
                raise HTTPException(409, "授权与当前暂停动作不一致")
            try:
                authorizations = ToolAuthorizations(self.operations)
                if request.decision == "renew":
                    state["error_info"]["operation_id"] = authorizations.renew(request.authorization_id, task_id, principal)
                else:
                    authorizations.decide(request.authorization_id, task_id, principal, request.decision, request.reason)
            except Fault as error:
                raise HTTPException(409 if error.category == "conflict" else 422, str(error)) from None
            if request.decision == "renew":
                next_node, execution = "finish", "idle"
            elif request.decision == "approved":
                state.update(status="queued", question=None, last_error=None, error_info=None, awaiting_node=None)
                next_node, execution = "propose_patch", "queued"
            else:
                state.update(status="failed", question=None, awaiting_node=None,
                             last_error="用户拒绝了外部写操作", error_info=None)
                next_node, execution = "finish", "idle"
            task["execution"] = execution
            progress.update(next_node=next_node, in_flight=None, interrupted=False,
                            committed_state=deepcopy(state), state_hash=digest(state))
            self.store.put(task)
        trace("mcp.authorization", request.decision, task_id=task_id,
              path=self.store.root / "tasks" / task_id / "trace.jsonl",
              details={"authorization_id": request.authorization_id, "principal": principal})
        if request.decision == "approved":
            self.submit(task_id, False)
        return self.store.get(task_id)

    def validate_task_identity(self, task):
        if self.deployment_mode == "untrusted" and (
                task["config"].get("deployment_mode") != "untrusted"
                or task["config"].get("execution_backend") != "docker"):
            raise Fault("deployment_boundary_changed", "不可信用户服务不能执行本地模式旧任务", category="permission")
        owner = self.accounts.owner(task["state"]["task_id"])
        principal = task["config"].get("principal", "local-user")
        if owner is None:
            if principal != "local-user":
                raise Fault("permission_denied", "任务缺少可信用户归属", category="permission")
            if task["config"].get("cli_task"):
                postgres = __import__("code_repair.repair_tools", fromlist=["PostgresKnowledgeBase"]).PostgresKnowledgeBase.from_environment()
                if ("postgres" if postgres else "sqlite") != task["config"].get("cli_knowledge_backend", "sqlite"):
                    raise Fault("configuration_changed", "CLI 知识库后端与原任务不一致", category="configuration")
                if postgres:
                    from psycopg.conninfo import conninfo_to_dict
                    identity = digest({key: value for key, value in conninfo_to_dict(postgres.dsn).items()
                                       if key not in {"password", "passfile"}})
                    if task["config"].get("cli_knowledge_identity") != identity:
                        raise Fault("configuration_changed", "CLI PostgreSQL 目标与原任务不一致", category="configuration")
                    return replace(postgres, regression_path=Path(task["config"]["knowledge_path"]))
                return KnowledgeBase(Path(task["config"]["knowledge_path"]), embedder=self.knowledge.embedder)
            return self.knowledge
        if principal != owner and not (principal == "local-user" and owner == self.accounts.legacy_owner()):
            raise Fault("permission_denied", "任务用户身份与持久化归属不一致", category="permission")
        knowledge = self.knowledge_for_user(owner)
        recorded = task["config"].get("knowledge_path")
        if recorded and Path(recorded).resolve() != knowledge.path.resolve():
            raise Fault("permission_denied", "任务长期记忆路径与用户归属不一致", category="permission")
        return knowledge

    def create(self, request: TaskInput, key: str | None = None, user_id=None):
        if self.deployment_mode == "untrusted":
            if (request.source_type != "pasted_code" and request.file_content is None
                    and request.project_files is None and request.archive_base64 is None):
                raise HTTPException(403, "不可信用户模式只允许上传或粘贴源码，禁止访问服务端本地路径")
            if self.execution_backend != "docker":
                raise HTTPException(403, "不可信用户模式禁止宿主执行")
        # Scope is checked before user knowledge lookup, source reads, and worker submission.
        require_software_request(request.error_report, request.test_target, request.code_text,
                                 request.path, request.file_name, source_type=request.source_type)
        key = key or uuid4().hex
        if not 8 <= len(key) <= 128 or not all(c.isalnum() or c in "-_" for c in key):
            raise HTTPException(422, "幂等键需为 8–128 位字母、数字、连字符或下划线")
        with self.lock:
            if user_id:
                self.accounts.knowledge_path(user_id)
            try:
                request_input = request.persisted_input()
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            existing = self.store.request_task(key, request_input, user_id)
            if existing:
                return existing
            return self._create(request, key, user_id)

    def _create(self, request, key, user_id=None):
        task_id = uuid4().hex
        directory = self.store.root / "tasks" / task_id
        input_dir = directory / "input"
        input_dir.mkdir(parents=True)
        source_path = Path(request.path.strip()).expanduser().absolute()
        project_mode = request.source_type in ("directory", "zip_archive")
        is_upload = request.file_content is not None or request.project_files is not None or request.archive_base64 is not None
        if user_id and not is_upload and request.source_type != "pasted_code":
            source_directory = source_path.resolve() if request.source_type in ("git_repository", "directory") else source_path.parent.resolve()
            if source_directory.is_relative_to(self.store.root) or self.store.root.is_relative_to(source_directory):
                raise HTTPException(403, "代码来源不能包含服务数据目录；请使用目录外的代码或上传文件")
        import_summary = None
        if project_mode:
            try:
                if request.project_files is not None:
                    project = import_uploaded_files([item.model_dump() for item in request.project_files])
                elif request.archive_base64 is not None:
                    project = import_zip(request.archive_base64)
                else:
                    project = import_local_directory(source_path)
                source_path = input_dir / "project"
                project.write(source_path)
                language = detect_project_language(source_path) if request.language == "auto" else request.language
                validate_execution_commands(language, request.test_command, request.reproduce_command, request.regression_command)
                import_summary = project.summary()
                source = {"type": "directory", "path": str(source_path), "language": language,
                          "input_kind": request.source_type, "retrieval_scope": {"language": language}}
            except (ValueError, OSError) as error:
                shutil.rmtree(directory, ignore_errors=True)
                error_info(error, operation="task.project_import")
                message = str(error) if isinstance(error, ValueError) else "项目导入失败，请检查文件夹权限或压缩包"
                raise HTTPException(422, message) from error
        if request.file_content is not None:
            source_path = input_dir / request.file_name
            source_path.write_text(request.file_content, encoding="utf-8")
        args = argparse.Namespace(
            python_file=source_path if request.source_type == "python_file" else None,
            code_file=source_path if request.source_type == "code_file" else None,
            repository=source_path if request.source_type == "git_repository" else None,
            code_text=request.code_text, revision=request.revision.strip() or None,
            language=request.language,
            code_name=request.file_name if request.source_type == "pasted_code" else None,
            test_targets=request.test_target,
            test_command=request.test_command,
            reproduce_command=request.reproduce_command,
            regression_command=request.regression_command,
        )
        try:
            if not project_mode:
                source = check_source(args)
        except (ValueError, OSError) as error:
            error_info(error, operation="task.input")
            raise HTTPException(422, "输入来源校验失败，请检查路径、文件编码或版本参数") from error
        source_root = (source_path if request.source_type in ("git_repository", "directory", "zip_archive") else
                       source_path.parent if request.source_type in ("python_file", "code_file") else input_dir)
        state = create_initial_state(task_id, source, request.error_report,
                                     code_text=request.code_text if request.source_type == "pasted_code" else None,
                                     remaining_seconds=request.runtime_seconds)
        if import_summary and import_summary["skipped_count"]:
            state["warnings"] = [f"项目导入已排除 {import_summary['skipped_count']} 项凭据、依赖或缓存路径。"]
        task = {
            "state": state, "execution": "queued", "review": None,
            "progress": initial_progress(),
            "source_label": request.file_name or request.path or "粘贴的代码",
            "config": {"source_root": str(source_root), "workspace": str(directory / "workspace"),
                       "test_targets": [request.test_target.strip() or "."], "checkpoint": None,
                       "test_command": list(split_command(request.test_command) if request.test_command else ()),
                       "reproduce_command": list(split_command(request.reproduce_command) if request.reproduce_command else ()),
                       "regression_command": list(split_command(request.regression_command) if request.regression_command else ()),
                       "language": source.get("language", request.language),
                       **({"project_import": import_summary} if import_summary else {}),
                       "execution_backend": self.execution_backend,
                       "deployment_mode": self.deployment_mode,
                       "effect_keys": {name: uuid4().hex for name in ("apply_patch", "save_case", "reject_patch")},
                       "principal": user_id or "local-user",
                       "knowledge_path": str(self.accounts.knowledge_path(user_id) if user_id else self.knowledge.path),
                       **({"user_memory_snapshot": self.personal_memory(user_id).list()} if user_id else {}),
                       "source_fingerprint": source_fingerprint(source, source_root),
                       "allowed_paths": [source_path.name] if request.source_type in ("python_file", "code_file") else
                                        [str(source.get("file_name", "target.py"))] if request.source_type == "pasted_code" else ["."]},
            "created_at": timestamp(), "updated_at": timestamp(),
        }
        knowledge = self.knowledge_for_user(user_id) if user_id else self.knowledge
        request_input = request.persisted_input()
        manifest, manifest_hash = freeze_execution_manifest(task, directory, request_input, knowledge)
        try:
            self.store.create_once(task, key, request_input, user_id)
        except sqlite3.IntegrityError:
            try:
                existing = self.store.request_task(key, request_input, user_id)
            finally:
                shutil.rmtree(directory, ignore_errors=True)
            if existing is None:
                raise
            return existing
        from code_repair.runtime_config import configuration_scope
        with configuration_scope(manifest):
            trace("task.create", "completed", key=key, task_id=task_id, path=directory / "trace.jsonl",
                  details={"manifest_hash": manifest_hash})
        self.submit(task_id, False)
        return self.store.get(task_id)

    def submit(self, task_id, reviewing):
        with self.submit_lock:
            if task_id in self.submitted:
                return
            if len(self.submitted) >= self.max_workers:
                return  # Remain durably queued; the dispatcher submits when capacity is free.
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

    def rerun(self, task_id, principal, key=None):
        """Create a new execution from the recorded baseline, never reuse review/authorization."""
        from code_repair.execution_manifest import write_once
        from code_repair.execution_replay import evidence
        from code_repair.runtime_config import configuration_scope
        key = key or uuid4().hex
        if not 8 <= len(key) <= 128 or not all(c.isalnum() or c in "-_" for c in key):
            raise HTTPException(422, "幂等键需为 8–128 位字母、数字、连字符或下划线")
        with self.lock:
            original = self.store.get_owned(task_id, principal)
            if original["execution"] != "idle":
                raise HTTPException(409, "请等待原任务暂停或结束后再重新执行")
            old_dir = self.store.root / "tasks" / task_id
            manifest = verify_execution_manifest(original, old_dir, runtime=True)
            params = {"rerun_of": task_id, "manifest_hash": original["config"]["execution_manifest_hash"]}
            existing = self.store.request_task(key, params, principal)
            if existing:
                return existing
            if manifest["task"].get("input_was_redacted"):
                raise HTTPException(409, "原始输入含已脱敏信息，不能声称可原样重新执行")
            old_config = original["config"]
            if not old_config.get("checkpoint"):
                raise HTTPException(409, "原任务未保存源码基线，无法重新执行")
            new_id = uuid4().hex
            directory = self.store.root / "tasks" / new_id
            workspace = directory / "workspace"
            with configuration_scope(manifest):
                checkpoint = create_checkpoint_workspace(Path(old_config["workspace"]), workspace,
                                                          revision=old_config["checkpoint"])
            actual_baseline = file_manifest(workspace, source=True)
            expected_baseline = original.get("progress", {}).get("baseline_manifest")
            if expected_baseline is None or actual_baseline != expected_baseline:
                raise HTTPException(409, "源码基线与原执行证据不一致")
            for filename in ("execution-manifest.json", "knowledge.snapshot.sqlite3", "mcp-config.snapshot.json"):
                with (directory / filename).open("xb") as target:
                    target.write((old_dir / filename).read_bytes())
            contract = old_dir / "mcp-tools.snapshot.json"
            if contract.is_file():
                with (directory / contract.name).open("xb") as target:
                    target.write(contract.read_bytes())
            state = deepcopy(manifest["task"]["initial_state"])
            state["task_id"] = new_id
            config = deepcopy(old_config)
            config.update(source_root=str(workspace), workspace=str(workspace), checkpoint=checkpoint,
                          effect_keys={name: uuid4().hex for name in ("apply_patch", "save_case", "reject_patch")},
                          user_memory_snapshot=deepcopy(manifest["task"]["memory"]),
                          mcp_config_path=str(directory / "mcp-config.snapshot.json"),
                          rerun_of=task_id, rerun_source_evidence=evidence(original, old_dir))
            progress = initial_progress()
            progress.update(next_node="baseline_reproduction", baseline_manifest=actual_baseline,
                            workspace_manifest=actual_baseline)
            task = {"state": state, "config": config, "progress": progress, "execution": "queued", "review": None,
                    "source_label": original["source_label"], "created_at": timestamp(), "updated_at": timestamp()}
            write_once(directory / "run-origin.json", {**params, "task_id": new_id,
                "semantics": "相同冻结配置再次调用模型与工具；输出及外部服务状态可能变化；外部写操作需重新授权"})
            self.store.create_once(task, key, params, principal)
            with configuration_scope(manifest):
                trace("task.rerun", "created", task_id=new_id, path=directory / "trace.jsonl", details=params)
        self.submit(new_id, False)
        return self.store.get(new_id)

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
        knowledge = self.validate_task_identity(task)
        workspace = Path(config["workspace"])
        if config.get("execution_manifest_path"):
            manifest = verify_execution_manifest(task, workspace.parent)
            knowledge = SnapshotKnowledge(knowledge, workspace.parent, manifest)
        toolbox = RepairToolbox(
            workspace_root=workspace, source_root=Path(config["source_root"]),
            allowed_paths=tuple(config["allowed_paths"]), test_targets=tuple(config["test_targets"]),
            test_command=tuple(config.get("test_command", ())) or None,
            reproduce_command=tuple(config.get("reproduce_command", ())) or None,
            regression_command=tuple(config.get("regression_command", ())) or None,
            language=str(config.get("language", task["state"]["source"].get("language", "auto"))),
            checkpoint_revision=config["checkpoint"], knowledge_base=knowledge,
            retrieval_scope=task["state"]["source"].get("retrieval_scope", {}),
            validation_report_path=workspace.parent / "validation.md",
            execution_backend=get_execution_backend(config.get("execution_backend", "host")),
            task_id=task["state"]["task_id"],
            principal=self.accounts.owner(task["state"]["task_id"]) or config.get("principal", "local-user"),
        )

        def baseline(state):
            # Reproduce exactly the frozen checkpoint, including supporting files and tests.
            record = reproduce(
                {"type": "git_repository", "path": str(workspace), "revision": config["checkpoint"],
                 "analysis_paths": config["allowed_paths"] if state["source"]["type"] in ("python_file", "code_file") else []},
                state["original_report"], work_root=workspace.parent / "reproduction",
                timeout=min(10.0, max(0.1, state["remaining_budget"]["seconds"])),
                execution_backend=config.get("execution_backend", "host"),
                task_id=state["task_id"], clarifications=state.get("clarifications", []),
                language=str(config.get("language", "auto")),
                reproduce_command=tuple(config.get("reproduce_command", ())) or None,
            )
            return {"matched": record.get("status") == "已复现，与报告一致", "record": record,
                    "question": record.get("question") or None}

        def context(state):
            result = toolbox.collect_context_for_state(state, include_experience=False)
            if not result["code_evidence"]:
                size = 0
                from code_repair.language_support import is_code_path
                for path in sorted(workspace.rglob("*")):
                    if not path.is_file() or not is_code_path(path):
                        continue
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
        return self.guard_services(task, services)

    def guard_services(self, task, services):
        config = task["config"]
        workspace = Path(config["workspace"])
        def effect(name, state, action):
            keys = config.setdefault("effect_keys", {})
            key = keys.setdefault(name, uuid4().hex)
            if name == "apply_patch":
                key += f":{state['attempt_count']}:{state['patch_hash']}"
            self.validate_task_identity(task)
            if name != "apply_patch" and (not task.get("review") or task["review"]["patch_hash"] != state["patch_hash"]
                    or task["review"]["decision"] != ("accepted" if name == "save_case" else "rejected")):
                raise Fault("review_required", "该副作用需要与补丁版本一致的人工审核记录", category="permission")
            if task.get("progress"):
                from code_repair.recovery_manager import effect_key, effect_parameters, probe_business
                from code_repair.operation_store import business_effect
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
            safe = node in ("prepare_workspace", "collect_context", "retrieve_experience", "review_patch")
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
            result = run_process([sys.executable, "-X", "utf8", "-m", "code_repair.repair_worker",
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
            result = run_process([sys.executable, "-X", "utf8", "-m", "code_repair.repair_worker",
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
        task = self.store.get(task_id)
        self.validate_task_identity(task)
        with task_configuration(task, self.store.root / "tasks" / task_id), memory_scope(task["config"].get("user_memory_snapshot", ())):
            return self._run_inline(task_id, reviewing, managed=managed)

    def _run_inline(self, task_id, reviewing, *, managed=False):
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
                        source_file=Path(source["path"]) if source["type"] in ("python_file", "code_file") else None,
                        pasted_code=task["state"]["code_text"],
                        pasted_filename=str(source.get("file_name", "target.py")))
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

    def configure_context(self, task, context, run_id):
        task_id = task["state"]["task_id"]
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
            trace("checkpoint", "committed", result={"state": task["state"],
                  "next_node": task["progress"]["next_node"]}, details={"node": node})

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

    def run_checkpointed_inline(self, task_id, reviewing, *, managed=False, run_id=None):
        task = self.store.get(task_id)
        self.validate_task_identity(task)
        with task_configuration(task, self.store.root / "tasks" / task_id), memory_scope(task["config"].get("user_memory_snapshot", ())):
            return self._run_checkpointed_inline(task_id, reviewing, managed=managed, run_id=run_id)

    def _run_checkpointed_inline(self, task_id, reviewing, *, managed=False, run_id=None):
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
            self.configure_context(task, context, run_id)
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
                        source_file=Path(source["path"]) if source["type"] in ("python_file", "code_file") else None,
                        pasted_code=task["state"]["code_text"],
                        pasted_filename=str(source.get("file_name", "target.py")))
                    if source_fingerprint(source, Path(config["source_root"])) != config["source_fingerprint"]:
                        raise Fault("source_changed", "创建工作区期间输入发生变化", category="conflict")
                    task["progress"]["workspace_manifest"] = file_manifest(Path(config["workspace"]), source=True)
                    task["progress"]["baseline_manifest"] = task["progress"]["workspace_manifest"]
                    self.store.finish_step(task, "prepare_workspace", task["state"], {}, "baseline_reproduction", run_id)
                context.check()
                from code_repair.workflow_checkpoint import task_checkpointer
                from code_repair.workflow_replay import recording_scope
                with recording_scope(self.store.root / "tasks" / task_id), \
                        task_checkpointer(self.store.root / "tasks" / task_id, self.store, task) as (saver, graph_config):
                    graph = build_repair_workflow(self.services(task), start_node=task["progress"]["next_node"],
                                                  checkpointer=saver)
                    # Recovery reconciles effects before a new epoch uses native graph persistence.
                    graph.invoke(task["state"], config=graph_config, durability="sync")
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
    public_hosts, public_origins = _public_web_allowlist()
    root = data_root or Path(os.environ.get("REPAIR_DATA_DIR", PROJECT_ROOT / "work" / "web"))
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
                    from code_repair.recovery_manager import RecoveryManager
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
            "business": 409, "conflict": 409, "validation": 422, "timeout": 504, "budget": 408}.get(error.category, 500)
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
        task_id = None
        path = root / "errors.jsonl"
        with diagnostic_scope(path=path, request_id=request_id, operation=f"http.{request.method}"):
            try:
                host = request.headers.get("host", "")
                if not _allowed_request_host(host, public_hosts):
                    raise Fault("host_denied", "请求 Host 未列入服务端允许列表", category="permission")
                origin = request.headers.get("origin")
                if origin and not _allowed_request_origin(origin, host, public_origins):
                    raise Fault("origin_denied", "请求来源不受支持", category="permission")
                public = request.url.path in ("/api/health", "/api/auth/register", "/api/auth/login")
                if request.url.path.startswith("/api/") and not public:
                    token = request.cookies.get(SESSION_COOKIE)
                    user = runner.accounts.session(token)
                    request.state.user = user
                    if request.headers.get("X-Account-ID") and request.headers["X-Account-ID"] != user["id"]:
                        raise Fault("account_changed", "账号已在其他页面切换，请重新登录", category="permission")
                    path = root / "users" / user["id"] / "errors.jsonl"
                    if match:
                        runner.store.get_owned(match[1], user["id"])
                        task_id = match[1]
                        path = root / "tasks" / task_id / "errors.jsonl"
                    if request.method not in ("GET", "HEAD", "OPTIONS") and not hmac.compare_digest(
                            request.headers.get("X-CSRF-Token", ""), csrf_token(token)):
                        raise HTTPException(403, "会话校验失败，请刷新页面后重试")
                with diagnostic_scope(path=path, task_id=task_id):
                    if request.method == "POST" and request.url.path == "/api/tasks":
                        limit = MAX_TASK_REQUEST_BYTES
                        try:
                            declared = int(request.headers.get("content-length", "0"))
                        except ValueError:
                            raise HTTPException(400, "请求大小声明无效") from None
                        if declared < 0 or declared > limit:
                            raise HTTPException(413, "项目上传请求不能超过 700 MB（含 Base64 和 JSON）")
                        chunks = []
                        size = 0
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > limit:
                                raise HTTPException(413, "项目上传请求不能超过 700 MB（含 Base64 和 JSON）")
                            chunks.append(chunk)
                        # Starlette replays this cached body to the JSON endpoint.
                        request._body = b"".join(chunks)
                    response = await call_next(request)
            except Fault as error:
                response = await fault_response(request, error)
            except StarletteHTTPException as error:
                response = await http_error_response(request, error)
            except Exception as error:
                response = await unexpected_error_response(request, error)
            response.headers["X-Request-ID"] = request_id
            if request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            return response

    def signed_in(user, request):
        token = runner.accounts.new_session(user["id"], request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"user": user, "csrf_token": csrf_token(token)})
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.post("/api/auth/register", status_code=201)
    def register(value: Credentials, request: Request):
        runner.accounts.limit("register", request.client.host if request.client else "local", value.username)
        response = signed_in(runner.accounts.register(value.username, value.password.get_secret_value()), request)
        response.status_code = 201
        return response

    @app.post("/api/auth/login")
    def login(value: Credentials, request: Request):
        runner.accounts.limit("login", request.client.host if request.client else "local", value.username)
        return signed_in(runner.accounts.login(value.username, value.password.get_secret_value()), request)

    @app.get("/api/auth/me")
    def me(request: Request):
        return {"user": request.state.user, "csrf_token": csrf_token(request.cookies[SESSION_COOKIE])}

    @app.post("/api/auth/logout")
    def logout(request: Request):
        runner.accounts.logout(request.cookies[SESSION_COOKIE])
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/memories")
    def memories(request: Request):
        return {"memories": runner.personal_memory(request.state.user["id"]).list()}

    def chats(request):
        return Conversations(runner.personal_memory(request.state.user["id"]))

    @app.get("/api/conversations")
    def list_conversations(request: Request):
        return {"conversations": chats(request).list()}

    @app.post("/api/conversations", status_code=201)
    def create_conversation(request: Request):
        return chats(request).create()

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request):
        return chats(request).get(conversation_id)

    @app.get("/api/conversations/{conversation_id}/trace")
    def conversation_trace(conversation_id: str, request: Request,
                           cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        memory = runner.personal_memory(request.state.user["id"])
        chats(request).get(conversation_id)
        path = memory.path.with_name(f"conversation-{digest(conversation_id)}.trace.jsonl")
        try:
            return read_trace_page(path, cursor=cursor, limit=limit)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/conversations/{conversation_id}/messages")
    def chat_message(conversation_id: str, value: ChatInput, request: Request):
        user_id = request.state.user["id"]
        lock = conversation_lock(user_id, conversation_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "该对话正在生成回复，请稍后查询")
        try:
            memory = runner.personal_memory(user_id)
            conversation_hash = digest(conversation_id)
            trace_path = memory.path.with_name(f"conversation-{conversation_hash}.trace.jsonl")
            response = None
            with execution_scope(120, task_id=f"conversation:{conversation_hash}",
                                 trace_path=trace_path, circuit_path=runner.store.root / "circuits.sqlite3",
                                 max_tokens=20_000, max_cost=0.10):
                with traced_call("conversation.message",
                                 parameters={"conversation_hash": conversation_hash,
                                             "request_id": value.request_id},
                                 input_data={"content": value.content}) as call:
                    response = Conversations(memory).send(conversation_id, value)
                    call["result"] = {
                        "reply": next((item["content"] for item in reversed(response["messages"])
                                        if item["role"] == "assistant"), ""),
                        "summary": response["summary"],
                        "pending_candidates": response["candidates"],
                    }
            return response
        finally:
            lock.release()

    @app.post("/api/conversations/{conversation_id}/candidates/{candidate_id}/accept")
    def accept_candidate(conversation_id: str, candidate_id: str, value: CandidateInput, request: Request):
        return chats(request).decide(conversation_id, candidate_id, value)

    @app.post("/api/conversations/{conversation_id}/candidates/{candidate_id}/reject")
    def reject_candidate(conversation_id: str, candidate_id: str, request: Request):
        return chats(request).decide(conversation_id, candidate_id)

    @app.post("/api/memories", status_code=201)
    def add_memory(value: MemoryInput, request: Request):
        store = runner.personal_memory(request.state.user["id"])
        identifier = store.save(value)
        return {"id": identifier, "memories": store.list()}

    @app.put("/api/memories/{memory_id}")
    def edit_memory(memory_id: str, value: MemoryInput, request: Request):
        store = runner.personal_memory(request.state.user["id"])
        store.save(value, memory_id)
        return {"memories": store.list()}

    @app.delete("/api/memories/{memory_id}")
    def delete_memory(memory_id: str, request: Request):
        store = runner.personal_memory(request.state.user["id"])
        store.delete(memory_id)
        return {"memories": store.list()}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/client-errors", status_code=202)
    def client_error(request: ClientFaultInput, http_request: Request):
        if request.task_id:
            runner.store.get_owned(request.task_id, http_request.state.user["id"])
        fault = Fault(f"client_{request.code}", "浏览器报告执行或连接故障", category="frontend",
                      next_action="检查浏览器状态及对应请求诊断")
        fault.error_id = request.error_id
        path = root / "tasks" / request.task_id / "errors.jsonl" if request.task_id else root / "users" / http_request.state.user["id"] / "errors.jsonl"
        with diagnostic_scope(path=path, task_id=request.task_id, operation=f"frontend.{request.code}"):
            info = error_info(fault)
        return {"error_id": info["error_id"], "recorded": info["diagnostics_persisted"]}

    @app.post("/api/tasks", status_code=202)
    def create(request: TaskInput, http_request: Request):
        return runner.create(request, http_request.headers.get("Idempotency-Key"), http_request.state.user["id"])

    @app.get("/api/requests/{key}")
    def request_status(key: str, request: Request):
        task = runner.store.request_task(key, user_id=request.state.user["id"])
        if task is None:
            raise HTTPException(404, "尚无该幂等键对应的任务")
        return runner.task_with_pending_operations(task)

    @app.get("/api/tasks/{task_id}/trace")
    def task_trace(task_id: str, request: Request, cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        runner.store.get_owned(task_id, request.state.user["id"])
        path = runner.store.root / "tasks" / task_id / "trace.jsonl"
        try:
            return read_trace_page(path, cursor=cursor, limit=limit)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/tasks/{task_id}/manifest")
    def task_manifest(task_id: str, request: Request):
        from code_repair.execution_replay import evidence
        task = runner.store.get_owned(task_id, request.state.user["id"])
        directory = runner.store.root / "tasks" / task_id
        manifest = verify_execution_manifest(task, directory)
        return {"manifest": manifest, "sha256": task["config"]["execution_manifest_hash"],
                "evidence": evidence(task, directory)}

    @app.post("/api/tasks/{task_id}/replay")
    def task_replay(task_id: str, request: Request):
        from code_repair.execution_replay import replay
        task = runner.store.get_owned(task_id, request.state.user["id"])
        if task["execution"] != "idle":
            raise HTTPException(409, "请等待任务暂停或结束后再回放")
        return replay(task, runner.store.root / "tasks" / task_id)

    @app.post("/api/tasks/{task_id}/rerun", status_code=202)
    def task_rerun(task_id: str, request: Request):
        return runner.rerun(task_id, request.state.user["id"], request.headers.get("Idempotency-Key"))

    @app.get("/api/tasks/{task_id}/stream")
    async def task_stream(task_id: str, request: Request, cursor: int = Query(0, ge=0), account_id: str = ""):
        """推送任务快照和持久化 Trace；断线后可用字节游标继续读取。"""
        user_id = request.state.user["id"]
        await run_in_threadpool(runner.store.get_owned, task_id, user_id)
        if account_id and account_id != user_id:
            raise Fault("account_changed", "账号已在其他页面切换，请重新登录", category="permission")
        last_event_id = request.headers.get("last-event-id", "").strip()
        if last_event_id:
            if not re.fullmatch(r"[0-9]{1,20}", last_event_id):
                raise HTTPException(422, "无效的 SSE 事件游标")
            cursor = int(last_event_id)
        path = runner.store.root / "tasks" / task_id / "trace.jsonl"
        try:
            await run_in_threadpool(read_trace_page, path, cursor=cursor, limit=100)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

        def read_update(position):
            # Long-lived connections must also stop after logout/session expiry.
            user = runner.accounts.session(request.cookies.get(SESSION_COOKIE))
            if user["id"] != user_id:
                raise HTTPException(401, "账号已切换，请重新登录")
            task = runner.task_with_pending_operations(runner.store.get_owned(task_id, user_id))
            return task, read_trace_page(path, cursor=position, limit=100)

        def event(name, payload, event_id=None):
            lines = []
            if event_id is not None:
                lines.append(f"id: {event_id}")
            lines.append(f"event: {name}")
            lines.append("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return "\n".join(lines) + "\n\n"

        async def generate():
            position = cursor
            last_updated = None
            last_heartbeat = time.monotonic()
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    task, page = await run_in_threadpool(read_update, position)
                    revision = task.get("updated_at") or digest(task)
                    if revision != last_updated:
                        last_updated = revision
                        yield event("task", task)
                    if page["next_cursor"] > position:
                        position = page["next_cursor"]
                        yield event("trace", page, position)
                    if page["has_more"]:
                        continue
                    if task.get("execution") == "idle":
                        yield event("complete", {"task": task, "next_cursor": position})
                        return
                    if time.monotonic() - last_heartbeat >= 10:
                        yield ": heartbeat\n\n"
                        last_heartbeat = time.monotonic()
                except HTTPException as error:
                    yield event("stream-error", {"detail": str(error.detail), "status": error.status_code})
                    return
                except ValueError as error:
                    yield event("stream-error", {"detail": str(error), "status": 422})
                    return
                except Exception as error:
                    with diagnostic_scope(path=path.with_name("errors.jsonl"), task_id=task_id):
                        info = error_info(error, operation="task.stream")
                    yield event("stream-error", {"detail": info["message"], "status": 503})
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
        })

    @app.get("/api/tasks/{task_id}/errors")
    def task_errors(task_id: str, request: Request):
        runner.store.get_owned(task_id, request.state.user["id"])
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
    def get(task_id: str, request: Request):
        return runner.task_with_pending_operations(runner.store.get_owned(task_id, request.state.user["id"]))

    @app.post("/api/tasks/{task_id}/clarification", status_code=202)
    def clarify_task(task_id: str, value: ClarificationInput, request: Request):
        runner.store.get_owned(task_id, request.state.user["id"])
        return runner.task_with_pending_operations(runner.clarify(task_id, value.answer))

    @app.post("/api/tasks/{task_id}/budget", status_code=202)
    def extend_task_budget(task_id: str, value: BudgetExtensionInput, request: Request):
        return runner.task_with_pending_operations(runner.extend_budget(task_id, value, request.state.user["id"]))

    @app.get("/api/dead-letters")
    def dead_letters(request: Request, limit: int = Query(100, ge=1, le=100)):
        from code_repair.dead_letters import list_owned
        return {"items": list_owned(runner.store, request.state.user["id"], limit)}

    @app.post("/api/dead-letters/{identifier}/decision")
    def decide_dead_letter(identifier: str, value: DeadLetterInput, request: Request):
        return runner.task_with_pending_operations(runner.decide_dead_letter(identifier, value, request.state.user["id"]))

    @app.get("/api/failure-regressions")
    def failure_regressions(request: Request):
        from code_repair.failure_regression import FailureRegressions, review_content_hash
        archive = FailureRegressions(runner.accounts.knowledge_path(request.state.user["id"]))
        return {"candidates": [{**item, "content_hash": review_content_hash(item)} for item in archive.list()]}

    @app.post("/api/failure-regressions/{candidate_id}/review")
    def review_failure_regression(candidate_id: str, value: KnowledgeReviewInput, request: Request):
        from code_repair.failure_regression import FailureRegressions
        user_id = request.state.user["id"]
        try:
            return FailureRegressions(runner.accounts.knowledge_path(user_id)).decide(
                candidate_id, **value.model_dump(), actor="user:" + user_id)
        except KeyError:
            raise HTTPException(404, "回归候选不存在") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/tasks/{task_id}/tool-outcome", status_code=202)
    def resolve_tool(task_id: str, value: ToolOutcomeInput, request: Request):
        runner.store.get_owned(task_id, request.state.user["id"])
        return runner.task_with_pending_operations(runner.resolve_tool_outcome(task_id, value))

    @app.post("/api/tasks/{task_id}/review", status_code=202)
    def review(task_id: str, request: ReviewInput, http_request: Request):
        runner.store.get_owned(task_id, http_request.state.user["id"])
        return runner.review(task_id, request)

    @app.post("/api/tasks/{task_id}/tool-authorization", status_code=202)
    def authorize_tool(task_id: str, value: ToolAuthorizationInput, request: Request):
        return runner.task_with_pending_operations(runner.authorize_tool(task_id, value, request.state.user["id"]))

    @app.get("/api/knowledge/stats")
    def knowledge_stats(request: Request):
        knowledge = runner.knowledge_for_user(request.state.user["id"])
        failures = knowledge.failure_count() if hasattr(knowledge, "failure_count") else 0
        return {"total": knowledge.count(), "failures": failures}

    @app.post("/api/knowledge/import")
    def knowledge_import(request: KnowledgeImportInput, http_request: Request):
        user_id = http_request.state.user["id"]
        with execution_scope(120, task_id="knowledge-import", circuit_path=root / "circuits.sqlite3",
                             trace_path=root / "users" / user_id / "trace.jsonl"):
            reviewer = runner.knowledge_reviewer(user_id)
            return reviewer.knowledge.import_cases(request.cases, "http-import", reviewer=reviewer)

    @app.get("/api/knowledge/candidates")
    def knowledge_candidates(request: Request, status: str = "needs_human", limit: int = Query(100, ge=1, le=100)):
        try:
            return {"candidates": runner.knowledge_reviewer(request.state.user["id"]).list(status, limit=limit)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/knowledge/candidates/{candidate_id}")
    def knowledge_candidate(candidate_id: str, request: Request):
        try:
            return runner.knowledge_reviewer(request.state.user["id"]).get(candidate_id)
        except KeyError:
            raise HTTPException(404, "待审案例不存在") from None

    @app.post("/api/knowledge/candidates/{candidate_id}/review")
    def knowledge_candidate_review(candidate_id: str, value: KnowledgeReviewInput, request: Request):
        user_id = request.state.user["id"]
        try:
            with execution_scope(120, task_id="knowledge-review", circuit_path=root / "circuits.sqlite3",
                                 trace_path=root / "users" / user_id / "trace.jsonl"):
                return runner.knowledge_reviewer(user_id).decide(candidate_id,
                    **value.model_dump(), actor="user:" + user_id)
        except KeyError:
            raise HTTPException(404, "待审案例不存在") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/knowledge/search")
    def knowledge_search(request: KnowledgeSearchInput, http_request: Request):
        user_id = http_request.state.user["id"]
        # Knowledge search may call Embedding; enforce scope before entering its execution scope.
        if request.query.strip():
            require_software_request(request.query, request.file_paths, request.dependency_versions)
        try:
            with execution_scope(120, task_id="knowledge-search", circuit_path=root / "circuits.sqlite3",
                                 trace_path=root / "users" / user_id / "trace.jsonl") as context:
                matches = runner.knowledge_for_user(user_id).search(
                    request.query, request.limit, request.min_similarity, outcome=request.outcome,
                    filters={
                        "repository_id": request.repository_id,
                        "file_paths": request.file_paths,
                        "dependency_versions": request.dependency_versions,
                    },
                )
                return {"matches": matches, "warnings": context.warnings}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
