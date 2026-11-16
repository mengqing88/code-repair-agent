"""Deadlines, typed faults, replay policy, durable circuits and redacted call traces."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import sqlite3
import subprocess
import time
from typing import Callable
from uuid import uuid4
from code_repair.diagnostics import diagnostic_scope, record_error, process_evidence
from code_repair.trace_store import append_trace, safe_trace_data, trace_context, trace_scope
from code_repair.runtime_config import active_manifest


_BUDGET_LABELS = {
    "runtime": "时间预算耗尽",
    "tokens": "模型 Token 预算耗尽",
    "cost": "模型费用预算耗尽",
    "tool_calls": "工具调用预算耗尽",
    "request_retries": "请求重试次数耗尽",
    "repair_attempts": "修复尝试次数耗尽",
}
_BUDGET_CODES = {
    "deadline_exceeded": "runtime",
    "token_budget_exceeded": "tokens",
    "cost_budget_exceeded": "cost",
    "tool_budget_exceeded": "tool_calls",
    "retry_budget_exceeded": "request_retries",
    "repair_attempts_exhausted": "repair_attempts",
}


def _infer_budget_kind(code, message=""):
    kind = _BUDGET_CODES.get(code)
    if kind:
        return kind
    text = f"{code} {message}"
    if "Token" in text or "token" in text:
        return "tokens"
    if "费用" in text or "cost" in text:
        return "cost"
    if "工具" in text or "tool" in text or "ReAct" in text:
        return "tool_calls"
    if "重试" in text or "retry" in text:
        return "request_retries"
    if "尝试" in text or "attempt" in text:
        return "repair_attempts"
    if "时间" in text or "deadline" in text or "timeout" in text:
        return "runtime"
    return None


def budget_fault(message, *, code=None, budget_kind=None, causes=None):
    """Create a budget fault with a stable machine-readable exhaustion dimension."""
    if not isinstance(code, str):
        code = None
    kind = budget_kind or _infer_budget_kind(code or "budget_exceeded", message)
    code = code or {
        "runtime": "deadline_exceeded", "tokens": "token_budget_exceeded", "cost": "cost_budget_exceeded",
        "tool_calls": "tool_budget_exceeded", "request_retries": "retry_budget_exceeded",
        "repair_attempts": "repair_attempts_exhausted",
    }.get(kind, "budget_exceeded")
    return Fault(code, message, category="budget", budget_kind=kind, causes=causes,
                 next_action="检查执行耗时，调整预算后新建任务" if kind == "runtime" else "检查任务记录后重试")


class Fault(RuntimeError):
    def __init__(self, code, message, *, category="internal", retryable=False,
                 outcome_unknown=False, next_action="检查任务记录后重试", retry_after=None, causes=None,
                 budget_kind=None, operation_id=None, issues=None, details=None):
        super().__init__(message)
        self.code, self.category, self.retryable = code, category, retryable
        self.outcome_unknown, self.next_action, self.retry_after = outcome_unknown, next_action, retry_after
        self.budget_kind = budget_kind or _infer_budget_kind(code, message) if category == "budget" else budget_kind
        self.operation_id = operation_id
        self.error_id, self.causes = uuid4().hex, causes or []
        self.issues, self.details = issues or [], details or {}

    def as_dict(self):
        result = {"code": self.code, "category": self.category, "message": str(self),
                "retryable": self.retryable, "outcome_unknown": self.outcome_unknown,
                "next_action": self.next_action, "retry_after": self.retry_after,
                "error_id": self.error_id, "causes": self.causes}
        if self.budget_kind:
            result["budget_kind"] = self.budget_kind
            result["budget_label"] = _BUDGET_LABELS.get(self.budget_kind, "预算耗尽")
        if self.operation_id:
            result["operation_id"] = self.operation_id
        if self.issues:
            result["issues"] = self.issues
        if self.details:
            result["details"] = self.details
        return result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Execution:
    seconds: float
    task_id: str = "local"
    trace_path: Path | None = None
    circuit_path: Path | None = None
    clock: Callable = time.monotonic
    started: float = field(init=False)
    tokens: int = 0
    cost: float = 0.0
    cost_known: bool = True
    usage_known: bool = True
    max_tokens: int | None = None
    max_cost: float | None = None
    prior_accounting: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    before_node: Callable | None = None
    after_node: Callable | None = None
    on_account: Callable | None = None
    on_request: Callable | None = None
    on_code_execution: Callable | None = None
    active_request: str | None = None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    workflow_ended: bool = False

    def __post_init__(self):
        self.started = self.clock()

    def remaining(self):
        return max(0.0, self.seconds - (self.clock() - self.started))

    def check(self):
        if self.remaining() <= 0:
            raise budget_fault("自动执行时间预算已耗尽", code="deadline_exceeded")
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            raise budget_fault("模型 Token 预算已耗尽", code="token_budget_exceeded")
        if self.max_cost is not None and self.cost >= self.max_cost:
            raise budget_fault("已统计模型费用达到预算上限", code="cost_budget_exceeded")


_current: ContextVar[Execution | None] = ContextVar("repair_execution", default=None)
_schema: ContextVar[dict | None] = ContextVar("repair_output_schema", default=None)


def current():
    return _current.get()


@contextmanager
def execution_scope(seconds, **kwargs):
    context = Execution(seconds, **kwargs)
    token = _current.set(context)
    try:
        path = context.trace_path.with_name("errors.jsonl") if context.trace_path else None
        with diagnostic_scope(path=path, task_id=context.task_id), trace_scope(call_id=context.run_id):
            yield context
    finally:
        _current.reset(token)


@contextmanager
def cleanup_scope():
    # Rollback must still be possible when the task's normal execution budget is exhausted.
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def output_schema(schema):
    token = _schema.set(schema)
    try:
        yield
    finally:
        _schema.reset(token)


def schema_context():
    return _schema.get()


def bounded_timeout(limit):
    ctx = current()
    if ctx:
        ctx.check()
        return max(0.001, min(limit, ctx.remaining()))
    return limit


def trace(operation, event, *, parameters=None, call_id=None, attempt=0, elapsed=0,
          fault=None, failure=None, decision=None, key=None, version="2", path=None, task_id=None,
          input_data=None, result=None, details=None, run_id=None, required=False):
    ctx = current()
    span = trace_context()
    identifier = call_id or span.get("call_id") or uuid4().hex
    info = error_info(fault, operation=operation) if fault is not None else failure
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "task_id": task_id or (ctx.task_id if ctx else "local"),
              "event_id": uuid4().hex, "run_id": run_id or (ctx.run_id if ctx else None),
              "call_id": identifier, "parent_call_id": span.get("parent_call_id") if identifier == span.get("call_id") else span.get("call_id"),
              "operation": operation, "version": version,
              "event": event, "attempt": attempt, "elapsed_ms": round(elapsed * 1000, 2) if elapsed is not None else None,
              "parameter_digest": digest(parameters) if parameters is not None else None,
              "idempotency_digest": digest(key) if key else None, "decision": decision,
              "error_code": info["code"] if info else None,
              "category": info["category"] if info else None, "error_id": info["error_id"] if info else None}
    manifest = active_manifest()
    # Supervisor and human-review events run outside the task's ContextVar scope.
    # Link those records to the same on-disk manifest as worker events.
    target = path or (ctx.trace_path if ctx else None)
    if manifest is None and target:
        manifest_path = Path(target).parent / "execution-manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or "version" not in manifest:
                    manifest = None
                    record["configuration_unavailable"] = True
            except (OSError, ValueError):
                record["configuration_unavailable"] = True
    if manifest is not None:
        record["manifest_hash"] = digest(manifest)
        record["configuration_version"] = manifest["version"]
    record.update({name: span[name] for name in (
        "node", "repair_attempt", "model_round", "model_call_id", "tool_name",
        "agent_role", "agent_label", "agent_node",
    ) if name in span})
    try:
        for name, value in (("input", input_data), ("result", result), ("details", details)):
            if value is not None:
                record[name] = safe_trace_data(value)
    except Exception as error:
        info = error_info(error, operation="trace.serialize")
        record["payload_error_id"] = info["error_id"]
        if ctx:
            ctx.warnings.append("Trace 内容序列化失败，请按错误编号检查诊断记录")
    if ctx:
        ctx.events.append(record)
    target = path or (ctx.trace_path if ctx else None)
    if target:
        try:
            append_trace(target, record)
        except Exception as error:
            diagnostic = error_info(error, operation="trace.write")
            if ctx and "Trace 写入失败，请按错误编号检查备用诊断记录" not in ctx.warnings:
                ctx.warnings.append("Trace 写入失败，请按错误编号检查备用诊断记录")
            if required:
                raise Fault("trace_persist_failed", "关键操作无法持久化 Trace，已阻止后续执行",
                            category="storage", next_action="修复审计存储后重新提交该操作",
                            causes=[diagnostic]) from error
    elif required:
        raise Fault("trace_target_missing", "关键操作没有持久化 Trace 目标，已阻止后续执行",
                    category="storage", next_action="配置任务 Trace 存储后重新提交该操作")
    return record


@contextmanager
def traced_call(operation, *, parameters=None, input_data=None, required=False, **labels):
    """Pair every started call with its result/failure, retaining the parent span."""
    ctx = current()
    required = required or bool(ctx and ctx.trace_path)
    with trace_scope(**labels):
        started = time.monotonic()
        trace(operation, "started", parameters=parameters, input_data=input_data, required=required)
        observation = {}
        try:
            yield observation
        except Exception as error:
            trace(operation, "failed", fault=error, elapsed=time.monotonic() - started,
                  result=observation.get("result"), details=observation.get("details"), required=required)
            raise
        else:
            failure = observation.get("failure")
            trace(operation, "failed" if failure else "succeeded", failure=failure,
                  elapsed=time.monotonic() - started,
                  result=observation.get("result"), details=observation.get("details"), required=required)


def trace_workflow_end(state, *, reason=None, path=None, run_id=None):
    ctx = current()
    info = state.get("error_info") or {}
    status = state.get("status")
    reason = reason or ("outcome_unknown" if info.get("outcome_unknown") else
        "awaiting_review" if status == "reviewable" else
        "awaiting_input" if status == "needs_input" else
        "accepted_and_saved" if status == "succeeded" else
        "budget_exhausted" if status == "budget_exceeded" else
        "review_rejected" if state.get("review_decision") == "rejected" else
        "validation_failed" if (state.get("validation") or {}).get("passed") is False else
        "execution_failed" if status == "failed" else "state_not_runnable")
    trace("workflow", "paused" if status in ("reviewable", "needs_input") else "finished",
          path=path, task_id=state["task_id"], run_id=run_id, failure=info or None,
          decision=status, details={"reason": reason, "message": state.get("last_error"), "question": state.get("question")},
          result={name: state.get(name) for name in ("status", "patch_hash", "attempt_count", "remaining_budget", "accounting", "memory_saved", "review_decision")})
    if ctx:
        ctx.workflow_ended = True


def charge_usage(tokens, cost):
    ctx = current()
    if ctx:
        ctx.tokens += tokens
        ctx.cost += cost or 0
        ctx.cost_known = ctx.cost_known and cost is not None and tokens > 0
        ctx.usage_known = ctx.usage_known and tokens > 0
        if ctx.on_request and ctx.active_request:
            ctx.on_request("charged", ctx.active_request, None)
            ctx.active_request = None
        if ctx.on_account:
            ctx.on_account(ctx)


def retry_after_seconds(headers, now=None):
    value = headers.get("retry-after") if headers else None
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            return max(0.0, date.timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError):
            return None


class Circuit:
    """SQLite circuit state survives worker restarts; one probe after cooldown."""
    def __init__(self, path: Path | None = None, threshold=3, cooldown=30):
        self.path, self.threshold, self.cooldown = path, threshold, cooldown
        self.memory = {}

    def _update(self, key, action):
        if self.path is None:
            state = self.memory.get(key, [0, 0.0])
            value, state = action(state)
            self.memory[key] = state
            return value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS circuits (id TEXT PRIMARY KEY, failures INTEGER, until REAL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT failures, until FROM circuits WHERE id=?", (key,)).fetchone()
            value, state = action(list(row) if row else [0, 0.0])
            db.execute("INSERT OR REPLACE INTO circuits VALUES (?, ?, ?)", (key, *state))
            db.commit()
            return value
        finally:
            db.close()

    def permit(self, key):
        def check(state):
            count, until = state
            if until > time.time():
                return False, state
            if count >= self.threshold:
                # A probe can occupy the full 60-second request timeout.
                return True, [count, time.time() + max(65, self.cooldown)]
            return True, state
        if not self._update(key, check):
            raise Fault("circuit_open", "外部服务暂时熔断，尚未发送请求", category="transient", retryable=True,
                        next_action="等待服务恢复后重试", retry_after=self.cooldown)

    def success(self, key):
        self._update(key, lambda _: (None, [0, 0.0]))

    def failure(self, key):
        def fail(state):
            count = state[0] + 1
            return None, [count, time.time() + self.cooldown if count >= self.threshold else 0.0]
        self._update(key, fail)


_DEFAULT_CIRCUIT = Circuit()


def call_with_retry(operation, call, *, classify, replay_safe, parameters=None, endpoint="local",
                    max_retries=2, sleep=time.sleep, jitter=random.uniform, circuit=None):
    ctx = current()
    trace_required = bool(ctx and ctx.trace_path)
    breaker = circuit or (Circuit(ctx.circuit_path) if ctx and ctx.circuit_path else _DEFAULT_CIRCUIT)
    key = digest([operation, endpoint])
    call_id = uuid4().hex
    local_deadline = time.monotonic() + 120
    for attempt in range(max_retries + 1):
        if ctx:
            ctx.check()
        elif time.monotonic() >= local_deadline:
            raise Fault("deadline_exceeded", "外部调用时间预算已耗尽", category="budget")
        try:
            breaker.permit(key)
        except Exception as fault:
            trace(operation, "failed", call_id=call_id, attempt=attempt, fault=fault,
                  decision="circuit_check_failed", required=trace_required)
            raise
        started = time.monotonic()
        trace(operation, "started", parameters=parameters, call_id=call_id, attempt=attempt,
              required=trace_required)
        request_id = uuid4().hex
        if ctx and ctx.on_request:
            ctx.on_request("started", request_id, operation)
            ctx.active_request = request_id
        try:
            result = call(bounded_timeout(min(60.0, max(0.001, local_deadline - time.monotonic()))))
        except Exception as error:
            fault = error if isinstance(error, Fault) else classify(error)
            exhausted_retries = (
                fault.category == "transient" and fault.retryable and replay_safe and attempt >= max_retries
            )
            if exhausted_retries:
                # Keep the provider's original code/message, but expose that the bounded retry budget ended.
                fault.budget_kind = "request_retries"
            fault._repair_diagnostic = record_error(error, fault.as_dict(), operation=operation)
            if ctx and ctx.on_request:
                ctx.on_request("unknown" if fault.category == "transient" or fault.outcome_unknown else "rejected", request_id, operation)
                ctx.active_request = None
            if ctx and (fault.outcome_unknown or fault.category == "transient"):
                ctx.cost_known = ctx.usage_known = False
            try:
                if fault.retryable and fault.category == "transient":
                    breaker.failure(key)
                else:
                    breaker.success(key)
            except Exception as bookkeeping_error:
                raise Fault("retry_state_failed", "请求失败且重试状态记录失败，已停止重试", category="storage",
                            causes=[error_info(fault), error_info(bookkeeping_error, operation="circuit.write")]) from bookkeeping_error
            left = ctx.remaining() if ctx else local_deadline - time.monotonic()
            delay = max(jitter(0, min(8.0, 0.5 * 2**attempt)), fault.retry_after or 0.0)
            retry = fault.category == "transient" and fault.retryable and replay_safe and attempt < max_retries and left > delay + 0.05
            trace(operation, "retry" if retry else "failed", call_id=call_id, attempt=attempt,
                  elapsed=time.monotonic() - started, fault=fault,
                  decision=f"backoff:{delay:.3f}s" if retry else "stop", required=trace_required)
            if not retry:
                raise fault from None
            sleep(delay)
        else:
            try:
                breaker.success(key)
            except Exception as error:
                error_info(error, operation="circuit.success")
            trace(operation, "succeeded", call_id=call_id, attempt=attempt,
                  elapsed=time.monotonic() - started, required=trace_required)
            return result


def kill_process_tree(process):
    if process.poll() is not None:
        return
    failures = []
    try:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
            if result.returncode and process.poll() is None:
                raise subprocess.CalledProcessError(result.returncode, ["taskkill"])
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as error:
        failures.append(error_info(error, operation="process.stop_tree"))
    # Still attempt to stop/reap the parent if tree cleanup failed.
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    except Exception as error:
        failures.append(error_info(error, operation="process.stop_parent"))
    if failures:
        raise Fault("process_stop_failed", "无法确认执行进程树已停止", category="unknown", outcome_unknown=True,
                    next_action="先人工确认进程树已停止，再恢复任务副本", causes=failures)


def process_identity(pid):
    """Creation identity prevents a restart from killing an unrelated reused PID."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                return None
            values = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
                return None
            return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def stop_recorded_worker(lease):
    if not lease or not lease.get("identity") or process_identity(lease["pid"]) != lease["identity"]:
        return
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(lease["pid"]), "/T", "/F"], capture_output=True, timeout=10)
        if result.returncode and process_identity(lease["pid"]) == lease["identity"]:
            raise Fault("worker_stop_failed", "无法停止上次执行进程", category="unknown", outcome_unknown=True)
    else:
        try:
            os.killpg(lease["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def recorded_worker_stopped(lease):
    """An inaccessible PID is unknown, not proof that the previous writer disappeared."""
    if not lease:
        return True
    if not lease.get("identity") or not isinstance(lease.get("pid"), int):
        return False
    pid = lease["pid"]
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such PID.
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            if code.value != 259:
                return True
            identity = process_identity(pid)
            return identity is not None and identity != lease["identity"]
        finally:
            kernel.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[0] == "Z" or stat[19] != lease["identity"]
    except FileNotFoundError:
        return True
    except (OSError, IndexError):
        return False


def windows_process_job(process):
    """Closing the job (including a parent crash) terminates all remaining descendants."""
    import ctypes
    from ctypes import wintypes
    class Limits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise Fault("process_isolation_failed", "无法创建任务进程组", category="internal")
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
        kernel.CloseHandle(handle)
        raise Fault("process_isolation_failed", "无法登记任务进程组", category="internal")
    def close():
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    return close


def resume_windows_process(process):
    """Popen closes the primary thread handle; resume via its owned process handle."""
    import ctypes
    from ctypes import wintypes
    native = ctypes.WinDLL("ntdll")
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = wintypes.LONG
    if native.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
        raise Fault("process_isolation_failed", "无法启动已隔离的任务进程", category="internal")


def run_process(command, *, timeout, on_started=None, **kwargs):
    """Interrupt the whole child tree, including pytest/spawned scripts, at a bounded deadline."""
    timeout = bounded_timeout(timeout)
    # No child code (including spawning descendants) may run before Job/lease registration.
    process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x4} if os.name == "nt" else {"start_new_session": True}
    expires_at = time.monotonic() + timeout
    input_data = kwargs.pop("input", None)
    capture_output = kwargs.pop("capture_output", False)
    check = kwargs.pop("check", False)
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input_data is not None:
        kwargs["stdin"] = subprocess.PIPE
    try:
        process = subprocess.Popen(command, **kwargs, **process_options)
    except Exception as error:
        error_info(error, operation="process.start")
        raise
    close_job = lambda: None
    try:
        if os.name == "nt":
            close_job = windows_process_job(process)
        if on_started:
            on_started(process)
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        if os.name == "nt":
            resume_windows_process(process)
        stdout, stderr = process.communicate(input_data, timeout=remaining)
    except BaseException as error:
        primary = error_info(error, operation="process.execute")
        failures = []
        for operation, cleanup in (("process.close_job", close_job),
                                   ("process.stop_tree", lambda: kill_process_tree(process)),
                                   ("process.drain", lambda: process.communicate(timeout=5))):
            try:
                cleanup()
            except Exception as cleanup_error:
                failures.append(error_info(cleanup_error, operation=operation))
        if failures:
            raise Fault("process_stop_failed", "执行失败且无法确认进程清理完成，已暂停", category="unknown",
                        outcome_unknown=True, next_action="先人工确认进程树已停止，再恢复任务副本",
                        causes=[primary, *failures]) from error
        raise
    try:
        close_job()
    except Exception as error:
        raise Fault("process_stop_failed", "执行已返回，但无法确认进程组清理结果", category="unknown",
                    outcome_unknown=True, causes=[error_info(error, operation="process.close_job")]) from error
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode:
        error = subprocess.CalledProcessError(result.returncode, command, output=stdout, stderr=stderr)
        error_info(error, operation="process.exit", details=process_evidence(stderr, result.returncode))
        if check:
            raise error
    return result


def error_info(error, *, operation=None, details=None):
    previous = getattr(error, "_repair_diagnostic", None)
    if previous:
        return dict(previous)
    fault = error
    if isinstance(error, BaseExceptionGroup):
        pending, children, scanned = [error], [], 0
        truncated = False
        while pending and scanned < 32 and len(children) < 8:
            child = pending.pop(0)
            scanned += 1
            if isinstance(child, BaseExceptionGroup):
                truncated = truncated or len(child.exceptions) > 32
                pending.extend(child.exceptions[:32])
            else:
                children.append(error_info(child, operation=operation))
        # Omitted failures may have unknown effects; a bounded capture must fail conservatively.
        uncertain = bool(pending) or truncated or any(child["outcome_unknown"] for child in children)
        fault = Fault("exception_group", "多个操作发生异常，请检查关联错误", category="unknown" if uncertain else "internal",
                      outcome_unknown=uncertain, causes=children)
    elif not isinstance(fault, Fault):
        cause, seen = error.__cause__, set()
        while cause is not None and id(cause) not in seen and len(seen) < 32:
            seen.add(id(cause))
            if isinstance(cause, Fault):
                fault = cause
                break
            cause = cause.__cause__
    if isinstance(fault, Fault):
        info = fault.as_dict()
    elif isinstance(error, subprocess.TimeoutExpired):
        info = Fault("process_timeout", "子进程执行超时，已请求停止进程树", category="timeout", next_action="检查运行日志、输入条件及超时预算").as_dict()
    elif isinstance(error, subprocess.CalledProcessError):
        info = Fault("process_failed", "子进程以非零状态退出", category="execution").as_dict()
    elif isinstance(error, sqlite3.Error):
        info = Fault("database_failed", "数据库操作失败", category="storage", next_action="检查数据库文件、磁盘空间与锁冲突").as_dict()
    elif isinstance(error, PermissionError):
        info = Fault("filesystem_permission", "文件或目录访问被拒绝", category="permission", next_action="检查文件权限与占用状态").as_dict()
    elif isinstance(error, FileNotFoundError):
        info = Fault("file_not_found", "所需文件、目录或可执行程序不存在", category="filesystem", next_action="检查输入路径和运行环境").as_dict()
    elif isinstance(error, (UnicodeError, ValueError)):
        info = Fault("invalid_arguments", "参数、编码或业务数据不符合要求", category="validation", next_action="修正参数或补充输入后重试").as_dict()
    elif isinstance(error, TimeoutError):
        info = Fault("operation_timeout", "操作等待超时", category="timeout").as_dict()
    elif isinstance(error, OSError):
        info = Fault("filesystem_failed", "系统或文件操作失败", category="filesystem", next_action="检查系统错误号、磁盘空间和文件占用").as_dict()
    else:
        info = Fault("execution_failed", "操作执行失败，请按错误编号检查诊断记录", category="internal").as_dict()
    ctx = current()
    path = ctx.trace_path.with_name("errors.jsonl") if ctx and ctx.trace_path else None
    with diagnostic_scope(path=path, task_id=ctx.task_id if ctx else None):
        return record_error(error, info, operation=operation, details=details)


def failure_update(error, *, operation=None, message=None):
    info = error_info(error, operation=operation)
    update = {"status": "needs_input" if info["outcome_unknown"] else "budget_exceeded" if info["category"] == "budget" else "failed",
              "last_error": message or info["message"], "error_info": info}
    if info.get("budget_kind"):
        update["budget_exhausted"] = info["budget_kind"]
    return update


def rollback_error(primary, error):
    causes = ([primary] if primary else []) + [error_info(error, operation="checkpoint.restore")]
    return Fault("rollback_failed", "执行失败且恢复任务副本失败，需人工检查", category="unknown", outcome_unknown=True,
                 next_action="先核对任务副本与原始故障，确认恢复结果后再处理", causes=causes)


def track_node(name, state, function):
    """Account network correction calls and time once, while preserving graph test injection."""
    ctx = current()
    if ctx is None:
        with execution_scope(max(0.001, state["remaining_budget"]["seconds"]), task_id=state["task_id"],
                             max_tokens=state["remaining_budget"]["tokens"], max_cost=state["remaining_budget"]["cost"]):
            return track_node(name, state, function)
    with trace_scope(node=name, repair_attempt=state.get("attempt_count", 0) + 1):
        return _track_node(name, state, function)


def _track_node(name, state, function):
    ctx = current()
    # A failed checkpoint must abort execution, never become a normal node result.
    if ctx.before_node:
        ctx.before_node(name, state)
    started, tokens, cost = time.monotonic(), ctx.tokens, ctx.cost
    call_id = trace_context()["call_id"]
    trace(name, "started", call_id=call_id, parameters={"task": state["task_id"], "patch": state.get("patch_hash")})
    try:
        with diagnostic_scope(operation=name):
            update = dict(function(state))
    except Exception as error:
        info = error_info(error, operation=name)
        update = {"status": "budget_exceeded" if info["category"] == "budget" else "failed",
                  "last_error": info["message"], "error_info": info}
    budget = dict(update.get("remaining_budget", state["remaining_budget"]))
    budget["seconds"] = min(budget["seconds"], ctx.remaining())
    budget["tokens"] = min(budget["tokens"], max(0, state["remaining_budget"]["tokens"] - (ctx.tokens - tokens)))
    budget["cost"] = min(budget["cost"], max(0.0, state["remaining_budget"]["cost"] - (ctx.cost - cost)))
    update["remaining_budget"] = budget
    prior = ctx.prior_accounting
    update["accounting"] = {"tokens_observed": ctx.tokens + prior.get("tokens_observed", 0),
                            "cost_observed_yuan": ctx.cost + prior.get("cost_observed_yuan", 0),
                            "cost_complete": ctx.cost_known, "usage_complete": ctx.usage_known}
    update["warnings"] = list(ctx.warnings)
    info = update.get("error_info")
    if info and info["outcome_unknown"]:
        update["status"] = "needs_input"
    elif info and info["category"] == "budget":
        update["status"] = "budget_exceeded"
    update["budget_exhausted"] = info.get("budget_kind") if info else None
    if budget["seconds"] <= 0 and not (info and info["outcome_unknown"]) and update.get("status") not in ("failed", "succeeded"):
        update.update(status="budget_exceeded", last_error="自动执行时间预算已耗尽",
                      error_info=error_info(budget_fault("自动执行时间预算已耗尽", code="deadline_exceeded",
                                                        causes=[info] if info else None), operation=name),
                      budget_exhausted="runtime")
    from code_repair.diagnostics import redact
    if update.get("last_error"):
        update["last_error"] = redact(update["last_error"])
    trace(name, "finished", call_id=call_id, elapsed=time.monotonic() - started, decision=update.get("status"),
          failure=update.get("error_info"), result=update)
    update["warnings"] = list(ctx.warnings)
    if ctx.after_node:
        ctx.after_node(name, state, update)
    return update
