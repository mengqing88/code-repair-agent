"""Startup recovery: freeze old writers, preserve A, verify/repair, commit B or restore A."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import time
from uuid import uuid4

from code_repair.knowledge_base import build_case_document, build_rejection_document
from code_repair.recovery_agent import RecoveryAgent
from code_repair.recovery_store import (RecoveryBackup, WORKFLOW_VERSION, boot_identity, file_manifest,
                            managed_task_path, source_fingerprint, assert_plain_path)
from code_repair.resilience import (Fault, cleanup_scope, digest, error_info, execution_scope, trace,
                        stop_recorded_worker, recorded_worker_stopped, trace_workflow_end)
from code_repair.diagnostics import diagnostic_scope
from code_repair.task_lifecycle import transition_scope


def effect_key(task, name, state=None):
    state = state or task["state"]
    key = task["config"]["effect_keys"][name]
    if name == "apply_patch":
        generation = task["progress"].get("apply_generation") or 0
        key += f":{state['attempt_count']}:{state['patch_hash']}:{generation}"
    return key


def effect_parameters(task, name, state=None):
    state = state or task["state"]
    result = {"task_id": state["task_id"], "principal": task["config"]["principal"],
              "checkpoint": task["config"]["checkpoint"], "patch_hash": state["patch_hash"],
              "allowed_paths": task["config"]["allowed_paths"]}
    if name != "apply_patch":
        document = build_case_document(state) if name == "save_case" else build_rejection_document(state)
        result["document_hash"] = digest(document)
    return result


def probe_business(task, name, state=None):
    """Four outcomes. Absence proves nonexecution only AFTER the old writer was fenced/stopped."""
    if task["config"].get("cli_knowledge_backend") == "postgres":
        # The local regression database is not evidence for a PostgreSQL write.
        return "unknown"
    state = state or task["state"]
    document = build_case_document(state) if name == "save_case" else build_rejection_document(state)
    path = Path(task["config"]["knowledge_path"])
    if not path.exists():
        return "not_applied"
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5) as db:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table, column = ("repair_cases", "case_id") if name == "save_case" else ("repair_reviews", "task_id")
        row = db.execute(f"SELECT document FROM {table} WHERE {column}=?", (state["task_id"],)).fetchone() if table in tables else None
        receipt = db.execute("SELECT task_id,name,params_hash,document_hash FROM effect_receipts WHERE key=?",
                             (effect_key(task, name, state),)).fetchone() if "effect_receipts" in tables else None
        if receipt:
            expected = (state["task_id"], name, digest(effect_parameters(task, name, state)), digest(document))
            return "completed" if tuple(receipt) == expected and row and row[0] == document else "conflict"
        if row:
            return "unknown" if row[0] == document else "conflict"
        return "not_applied"


class RecoveryManager:
    MAX_RECOVERIES = 3

    def __init__(self, runner, agent=None):
        self.runner, self.store = runner, runner.store
        self.agent = agent or RecoveryAgent()

    def recover_pending(self, task_ids=None):
        for task in self.store.list_tasks():
            if task_ids is not None and task["state"]["task_id"] not in task_ids:
                continue
            if not task.get("progress"):
                continue
            with self.store.connect() as db:
                job = db.execute("SELECT id FROM recovery_jobs WHERE task_id=? AND phase IN ('prepared','working','rolling_back','rolled_back') ORDER BY rowid DESC LIMIT 1",
                                 (task["state"]["task_id"],)).fetchone()
            progress = task["progress"]
            interrupted = progress.get("interrupted") or task["execution"] == "running" or progress["run_id"] is not None
            active = bool(job) and (progress.get("recovery") or {}).get("status") not in ("paused", "resumed")
            if interrupted or active or task.get("worker_stop_unconfirmed"):
                self.recover(task["state"]["task_id"], job[0] if job else None)

    def _trace(self, task_id, event, *, state=None, **kwargs):
        trace("task.recovery", event, task_id=task_id,
              path=self.store.root / "tasks" / task_id / "trace.jsonl", **kwargs)
        if state is not None:
            trace_workflow_end(state, path=self.store.root / "tasks" / task_id / "trace.jsonl",
                               reason="recovery_paused" if event == "paused" else None)

    def _stop_and_charge(self, task):
        self.store.revoke(task)
        task["worker_stop_unconfirmed"] = True
        self.store.put(task)
        stop_recorded_worker(task.get("worker"))
        if not recorded_worker_stopped(task.get("worker")):
            raise Fault("worker_stop_unconfirmed", "无法确认旧 worker 已停止，不能开始恢复", category="unknown", outcome_unknown=True)
        if task["config"].get("execution_backend") == "docker":
            from code_repair.docker_executor import cleanup_docker_task
            if not cleanup_docker_task(task["state"]["task_id"]):
                raise Fault("sandbox_cleanup_failed", "无法确认旧任务容器已停止", category="unknown", outcome_unknown=True)
        clock = task["progress"].get("clock")
        if clock:
            left = task["state"]["remaining_budget"]["seconds"]
            boot = boot_identity()
            elapsed = max(0, time.monotonic() - clock["tick"]) if boot and boot == clock["boot"] else left
            task["state"]["remaining_budget"]["seconds"] = max(0, left - elapsed)
            task["state"]["elapsed_seconds"] += min(left, elapsed)
            task["progress"]["clock"] = None
        task["worker_stop_unconfirmed"] = False
        self.store.put(task)

    @transition_scope("recovery")
    def recover(self, task_id, job_id=None):
        backup = None
        with diagnostic_scope(path=self.store.root / "tasks" / task_id / "errors.jsonl", task_id=task_id, operation="task.recovery"):
            try:
                task = self.store.get(task_id)
                self.runner.validate_task_identity(task)
                self._stop_and_charge(task)
                if job_id:
                    candidate = RecoveryBackup(self.store, job_id)
                    knowledge = self.runner.validate_task_identity(candidate.snapshot["task"])
                    if (candidate.task_id != task_id
                            or Path(candidate.snapshot["business_path"]).resolve() != Path(getattr(knowledge, "path", None) or task["config"]["knowledge_path"]).resolve()):
                        raise Fault("permission_denied", "恢复快照归属或业务路径不一致，拒绝覆盖", category="permission")
                    backup = candidate
                    # Restore exactly the original A even when a previous rollback stopped halfway.
                    task = backup.restore()
                    self.runner.validate_task_identity(task)
                    self._trace(task_id, "rolled_back", key=job_id)
                if task["progress"]["recovery_count"] >= self.MAX_RECOVERIES:
                    raise Fault("recovery_limit", "自动恢复次数已达上限", category="budget")
                task["progress"]["recovery_count"] += 1
                self.store.put(task)
                if backup is None:
                    backup = RecoveryBackup.capture(self.store, task)
                self._trace(task_id, "snapshot_prepared", key=backup.job_id)
                run_id = uuid4().hex
                task["progress"].update(run_id=run_id, recovery={"id": backup.job_id, "status": "working"})
                task["execution"] = "running"
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.store.allocate_epoch(db, task)
                    self.store._write(db, task, expected_run=None)
                backup.set_phase("working")
                with execution_scope(task["state"]["remaining_budget"]["seconds"], task_id=task_id, run_id=run_id,
                        trace_path=self.store.root / "tasks" / task_id / "trace.jsonl",
                        circuit_path=self.store.root / "circuits.sqlite3",
                        max_tokens=task["state"]["remaining_budget"]["tokens"], max_cost=task["state"]["remaining_budget"]["cost"]) as context:
                    context.prior_accounting = task["state"].get("accounting", {})
                    context.cost_known = context.prior_accounting.get("cost_complete", True)
                    context.usage_known = context.prior_accounting.get("usage_complete", True)
                    context.on_account = lambda ctx: self.store.account(task, ctx)
                    def request(event, identifier, operation):
                        if event == "started":
                            task["progress"]["pending_calls"].append({"id": identifier, "operation": operation,
                                "status": "pending", "reserved_tokens": context.max_tokens - context.tokens,
                                "reserved_cost": context.max_cost - context.cost})
                        elif event in ("charged", "rejected"):
                            task["progress"]["pending_calls"] = [call for call in task["progress"]["pending_calls"] if call["id"] != identifier]
                        self.store.account(task, context)
                    context.on_request = request
                    try:
                        self._verify_and_repair(task, backup)
                    finally:
                        self.store.account(task, context)
                self.store.assert_owner(task, run_id)
                task["progress"].update(in_flight=None, interrupted=False, clock=None,
                    recovery={"id": backup.job_id, "status": "resumed"},
                    workspace_manifest=file_manifest(Path(task["config"]["workspace"]), source=True))
                task["progress"]["committed_state"] = deepcopy(task["state"])
                task["progress"]["state_hash"] = digest(task["state"])
                task["progress"].setdefault("revoked_runs", []).append(run_id)
                task["progress"]["run_id"] = None
                task["execution"] = "idle" if task["progress"]["next_node"] == "finish" else "queued"
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.store._write(db, task, expected_run=run_id)
                    db.execute("UPDATE recovery_jobs SET phase='committed' WHERE id=?", (backup.job_id,))
                self._trace(task_id, "committed", key=backup.job_id, decision=task["progress"]["next_node"],
                            state=task["state"] if task["progress"]["next_node"] == "finish" else None)
            except Exception as error:
                primary = error_info(error, operation="task.recovery")
                try:
                    if backup:
                        with self.store.connect() as db:
                            committed = db.execute("SELECT phase FROM recovery_jobs WHERE id=?", (backup.job_id,)).fetchone()
                        saved = self.store.get(task_id)
                        if (committed and committed[0] == "committed"
                                and saved["progress"].get("recovery") == {"id": backup.job_id, "status": "resumed"}):
                            # The atomic task/job commit succeeded; a lost acknowledgement is not a rollback request.
                            return
                    task = self.store.get(task_id)
                    self.store.revoke(task)
                    if backup and backup.phase != "capturing":
                        task = backup.restore()
                    task["execution"] = "idle"
                    task["progress"]["interrupted"] = False
                    task["progress"]["recovery"] = {"id": backup.job_id if backup else None, "status": "paused", "error": primary}
                    task["state"].update(status="needs_input", last_error="自动恢复已暂停：" + primary["message"],
                                         error_info=primary, question="请按错误记录核对原任务；系统没有自动重放不确定操作。")
                    self.store.put(task)
                    self._trace(task_id, "paused", failure=primary, decision="restore_A", state=task["state"])
                except Exception as restore_error:
                    # Keep the job in rolling_back/prepared for the next startup; never claim a successful rollback.
                    secondary = error_info(restore_error, operation="task.recovery.rollback")
                    try:
                        task = self.store.get(task_id)
                        task["execution"] = "idle"
                        task["progress"]["interrupted"] = True
                        task["progress"]["recovery"] = {"id": backup.job_id if backup else None, "status": "rollback_pending"}
                        task["state"].update(status="needs_input", last_error="恢复回滚尚未完成，保留原始 A 等待核对。",
                            error_info=error_info(Fault("recovery_rollback_failed", "恢复失败且覆盖 A 尚未完成", category="unknown",
                                                       outcome_unknown=True, causes=[primary, secondary])))
                        self.store.put(task)
                        self._trace(task_id, "paused", failure=task["state"].get("error_info"),
                                    decision="rollback_pending", state=task["state"])
                    except Exception as persist_error:
                        error_info(persist_error, operation="task.recovery.persist")

    def _reset_state(self, task, state):
        previous = task["state"]
        task["state"] = deepcopy(state)
        for key, value in previous["remaining_budget"].items():
            task["state"]["remaining_budget"][key] = min(value, task["state"]["remaining_budget"][key])
        for key in ("accounting", "warnings", "elapsed_seconds"):
            if key in previous:
                task["state"][key] = previous[key]

    def _advice(self, task, error, allowed):
        count = task["progress"].get("agent_calls") or 0
        if count >= 2:
            raise Fault("recovery_agent_limit", "恢复助手调用次数已达上限", category="budget")
        task["progress"]["agent_calls"] = count + 1
        self.store.put(task)
        evidence = {"checkpoint": {"version": task["progress"]["version"], "valid": True},
                    "workspace": {"checkpoint_present": bool(task["config"].get("checkpoint"))},
                    "business": {"error_code": error.code}}
        advice = self.agent.advise(evidence, {*allowed, "pause"})
        self._trace(task["state"]["task_id"], "agent_advice", parameters=advice, decision=advice["action"])
        if advice["action"] == "pause":
            raise error
        return advice["action"]

    def _verify_and_repair(self, task, backup):
        from code_repair.repair_workflow import NODES
        from code_repair.repair_tools import restore_checkpoint_workspace, apply_patch
        progress, config = task["progress"], task["config"]
        if task.get("redelivery_restore"):
            raise Fault("dead_letter_restore_interrupted", "失败任务重投的文件恢复曾中断，需人工核对任务副本",
                        category="unknown", outcome_unknown=True)
        pending = self.runner.operations.pending_for_task(task["state"]["task_id"])
        if pending:
            raise Fault("mcp_outcome_unknown", "仍有未核实的外部写操作，禁止自动续跑", category="unknown", outcome_unknown=True,
                        operation_id=pending[0]["operation_id"], next_action="先核对外部操作结果")
        expected_workspace = managed_task_path(self.store, task["state"]["task_id"]) / "workspace"
        assert_plain_path(Path(config["workspace"]))
        if Path(config["workspace"]).absolute() != expected_workspace.absolute():
            raise Fault("recovery_path_denied", "恢复只允许修改当前任务的独立工作区", category="permission")
        self.runner.validate_task_identity(task)
        if progress["version"] != WORKFLOW_VERSION or progress["next_node"] not in (*NODES, "prepare_workspace", "finish"):
            raise Fault("workflow_version_mismatch", "工作流版本或续执行节点不兼容", category="conflict")
        if progress["pending_calls"]:
            for call in progress["pending_calls"]:
                if call.get("reservation_charged"):
                    continue
                task["state"]["remaining_budget"]["tokens"] = max(0, task["state"]["remaining_budget"]["tokens"] - call["reserved_tokens"])
                task["state"]["remaining_budget"]["cost"] = max(0, task["state"]["remaining_budget"]["cost"] - call["reserved_cost"])
                call["reservation_charged"] = True
            self.store.put(task)
            raise Fault("usage_unknown", "中断请求的用量无法核实，保留预算占用并暂停", category="unknown", outcome_unknown=True)
        if task["state"]["remaining_budget"]["seconds"] <= 0 and progress["next_node"] != "finish":
            raise Fault("deadline_exceeded", "剩余执行时间已耗尽，不能自动续跑", category="budget")
        attempt = progress.get("in_flight")
        if attempt:
            if attempt["input_hash"] != digest(attempt["input_state"]) or attempt["node"] != progress["next_node"]:
                raise Fault("checkpoint_conflict", "节点意图与输入摘要不匹配", category="conflict")
            self._reset_state(task, attempt["input_state"])
            node = attempt["node"]
            if node in ("baseline_reproduction", "validate_patch") and attempt.get("code_execution_started") is not False:
                raise Fault("test_outcome_unknown", "中断节点运行过用户代码，缺少可安全重放的证据", category="unknown", outcome_unknown=True)
            if node == "propose_patch" and attempt.get("model_tool_policy") not in {"read_only", "journaled"}:
                raise Fault("model_outcome_unknown", "模型节点未提交结果，不能确认中断调用的用量和输出", category="unknown", outcome_unknown=True)
            if node == "prepare_workspace":
                if source_fingerprint(task["state"]["source"], Path(config["source_root"])) != config["source_fingerprint"]:
                    raise Fault("source_changed", "原输入已变化，不能重建工作区", category="conflict")
                root = managed_task_path(self.store, task["state"]["task_id"])
                for name in ("workspace", ".workspace-repository.git", ".workspace-repair-checkpoint.json"):
                    path = root / name
                    if path.is_dir():
                        shutil.rmtree(path, onerror=lambda function, target, info: (Path(target).chmod(0o700), function(target)))
                    elif path.exists():
                        path.unlink()
                config["checkpoint"] = None
                return
            if node in ("save_case", "reject_patch"):
                name = "save_case" if node == "save_case" else "reject_patch"
                self.runner.durable_operations.lookup(effect_key(task, name), task_id=task["state"]["task_id"],
                    name=name, parameters=effect_parameters(task, name))
                review = task.get("review")
                if not review or review["patch_hash"] != task["state"]["patch_hash"] or review["decision"] != ("accepted" if name == "save_case" else "rejected"):
                    raise Fault("review_required", "恢复缺少一致的人工审核记录", category="permission")
                try:
                    outcome = probe_business(task, name)
                except (sqlite3.Error, OSError) as query_error:
                    error = Fault("recovery_query_failed", "业务状态查询失败", category="storage", causes=[error_info(query_error)])
                    self._advice(task, error, {"retry_query"})
                    outcome = probe_business(task, name)
                if outcome in ("unknown", "conflict"):
                    error = Fault("business_conflict" if outcome == "conflict" else "evidence_missing",
                                  "业务实际内容冲突" if outcome == "conflict" else "缺少可信业务回执", category="conflict")
                    self._advice(task, error, {"retry_query"})
                    outcome = probe_business(task, name)
                    if outcome in ("unknown", "conflict"):
                        raise error
                if name == "reject_patch":
                    with cleanup_scope():
                        restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                    if file_manifest(Path(config["workspace"]), source=True) != progress["baseline_manifest"]:
                        raise Fault("workspace_conflict", "拒绝后的工作区未恢复到可信基线", category="conflict")
                if outcome == "completed":
                    task["state"].update(status="succeeded" if name == "save_case" else "failed", memory_saved=name == "save_case",
                                         error_info=None, last_error=None if name == "save_case" else "用户拒绝了候选修复补丁")
                    progress["next_node"] = "finish"
                else:
                    row = self.runner.durable_operations.lookup(effect_key(task, name))
                    if row and row["status"] == "failed":
                        raise Fault("operation_failed", "原业务操作已明确失败，不能原样重试", category="business")
                    if row:
                        self.runner.durable_operations.finish(effect_key(task, name), "not_applied")
                return
            if node == "apply_patch":
                with cleanup_scope():
                    restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                if file_manifest(Path(config["workspace"]), source=True) != progress["baseline_manifest"]:
                    raise Fault("baseline_conflict", "任务基线内容不一致，拒绝重放补丁", category="conflict")
                state = task["state"]
                import hashlib
                if hashlib.sha256(state["patch_diff"].encode()).hexdigest() != state["patch_hash"]:
                    raise Fault("patch_conflict", "补丁内容与记录哈希冲突", category="conflict")
                progress["apply_generation"] = (progress.get("apply_generation") or 0) + 1
                self.store.put(task)
                self.runner.durable_operations.execute(effect_key(task, "apply_patch"), state["task_id"], "apply_patch",
                    effect_parameters(task, "apply_patch"),
                    lambda: apply_patch(state["patch_diff"], workspace_root=Path(config["workspace"]),
                        allowed_paths=config["allowed_paths"], protected_test_targets=config["test_targets"]),
                    lambda: (False, None))
                state.update(status="validating", error_info=None, last_error=None)
                progress["next_node"] = "validate_patch"
                return
        else:
            if progress.get("committed_state"):
                if digest(progress["committed_state"]) != progress["state_hash"]:
                    raise Fault("checkpoint_conflict", "已提交节点状态摘要不一致", category="conflict")
                self._reset_state(task, progress["committed_state"])
        if progress["next_node"] != "prepare_workspace":
            if file_manifest(Path(config["workspace"]), source=True) != progress.get("workspace_manifest"):
                error = Fault("workspace_conflict", "工作区与已提交节点内容冲突", category="conflict")
                # Rebuilding a validated patch would erase its validation evidence; do not allow that.
                allowed = {"rebuild_workspace"} if progress["next_node"] == "apply_patch" else set()
                self._advice(task, error, allowed)
                with cleanup_scope():
                    restore_checkpoint_workspace(Path(config["workspace"]), config["checkpoint"])
                if file_manifest(Path(config["workspace"]), source=True) != progress["baseline_manifest"]:
                    raise error
