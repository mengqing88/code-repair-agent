"""Crash injection uses real SQLite and files; no paid model or external business calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from tests.test_account_support import TestClient

from code_repair.knowledge_base import KnowledgeBase
from code_repair.operation_store import business_effect
from code_repair.recovery_agent import RecoveryAgent
from code_repair.recovery_manager import RecoveryManager, effect_key, effect_parameters, probe_business
from code_repair.recovery_store import RecoveryBackup, ServiceLock, file_manifest, initial_progress, filesystem_path
from code_repair.repair_api import RepairRunner, create_app, timestamp
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import create_checkpoint_workspace
from code_repair.repair_workflow import RepairServices
from code_repair.resilience import Fault, charge_usage

PATCH = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")
    value = RepairRunner(tmp_path / "data")
    value.worker_mode = "inline"
    value.knowledge = KnowledgeBase(value.store.root / "knowledge.sqlite3", embedder=lambda _: [1.0, 0.0])
    yield value
    value.dispatch_stop.set()
    value.pool.shutdown(wait=True)


def task_at(runner, *, workspace=False, node="prepare_workspace", status="queued", remaining_tokens=200000):
    identifier = uuid4().hex
    directory = runner.store.root / "tasks" / identifier
    (directory / "input").mkdir(parents=True)
    state = create_initial_state(identifier, {"type": "pasted_code"}, "value should be 2",
                                 code_text="value = 1\n", remaining_seconds=60,
                                 remaining_tokens=remaining_tokens)
    state.update(status=status, diagnosis="wrong value", patch_diff=PATCH,
                 patch_hash=hashlib.sha256(PATCH.encode()).hexdigest())
    task = {"state": state, "config": {"workspace": str(directory / "workspace"),
        "source_root": str(directory / "input"), "checkpoint": None, "source_fingerprint": {},
        "knowledge_path": str(runner.knowledge.path), "execution_backend": "host",
        "principal": "local-user", "allowed_paths": ["target.py"], "test_targets": ["."],
        "effect_keys": {name: uuid4().hex for name in ("apply_patch", "save_case", "reject_patch")}},
        "execution": "queued", "review": None, "source_label": "fixture",
        "progress": initial_progress(), "created_at": timestamp()}
    if workspace:
        task["config"]["checkpoint"] = create_checkpoint_workspace(directory / "input", directory / "workspace", pasted_code="value = 1\n")
        task["progress"]["baseline_manifest"] = file_manifest(directory / "workspace", source=True)
        task["progress"]["workspace_manifest"] = task["progress"]["baseline_manifest"]
    task["progress"]["next_node"] = node
    runner.store.put(task)
    return task


def in_flight(runner, task, node):
    task, token = runner.store.claim(task["state"]["task_id"])
    task["execution"] = "running"
    runner.store.begin_step(task, node, task["state"], token)
    return task, token


def no_agent(*args):
    pytest.fail("unexpected paid recovery model call")


def manager(runner):
    return RecoveryManager(runner, RecoveryAgent(no_agent))


def test_revision_and_execution_identity_reject_stale_worker(runner):
    task = task_at(runner)
    old, token = runner.store.claim(task["state"]["task_id"])
    stale = deepcopy(old)
    runner.store.put(old)
    with pytest.raises(Fault, match="版本"):
        runner.store.put(stale)
    runner.store.revoke(old)
    old["execution"] = "queued"
    runner.store.put(old)
    new, second = runner.store.claim(task["state"]["task_id"])
    assert second != token
    with pytest.raises(Fault, match="资格"):
        runner.store.assert_owner(stale, token)
    runner.background_failure(task["state"]["task_id"], RuntimeError("late old callback"), "task.background", run_id=token)
    assert runner.store.get(task["state"]["task_id"])["progress"]["run_id"] == second


def test_backup_overwrites_files_shapes_and_preserves_audit_and_budget(runner):
    task = task_at(runner)
    root = Path(task["config"]["workspace"]).parent
    (root / "file").write_text("original partial state")
    (root / "folder").mkdir()
    (root / "folder" / "child").write_text("before")
    (root / "empty").mkdir()
    (root / "errors.jsonl").write_text("old\n")
    backup = RecoveryBackup.capture(runner.store, task)
    (root / "file").unlink()
    (root / "file").mkdir()
    (root / "file" / "new").write_text("after")
    (root / "folder" / "child").unlink()
    (root / "folder").rmdir()
    (root / "folder").write_text("changed type")
    (root / "extra").write_text("added")
    (root / "errors.jsonl").write_text("old\nnew error\n")
    task["state"]["remaining_budget"].update(tokens=17, seconds=4, cost=0.001)
    task["progress"]["agent_calls"] = 2
    runner.store.put(task)
    original_revision = task["progress"]["revision"]
    restored = backup.restore()
    assert (root / "file").read_text() == "original partial state"
    assert (root / "folder" / "child").read_text() == "before"
    assert (root / "empty").is_dir() and not (root / "extra").exists()
    assert (root / "errors.jsonl").read_text().endswith("new error\n")
    assert restored["state"]["remaining_budget"]["tokens"] == 17
    assert restored["progress"]["revision"] > original_revision
    assert restored["progress"]["agent_calls"] == 2


def test_rollback_can_be_interrupted_and_repeated_from_same_a(runner, monkeypatch):
    task = task_at(runner)
    root = Path(task["config"]["workspace"]).parent
    (root / "a").write_text("A1")
    (root / "b").write_text("A2")
    backup = RecoveryBackup.capture(runner.store, task)
    (root / "a").write_text("partial recovery")
    (root / "b").write_text("partial recovery")
    from code_repair import recovery_store
    original = recovery_store.os.replace
    for _ in range(2):
        calls = []
        def crash(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("simulated process death during overwrite")
            return original(source, target)
        with monkeypatch.context() as scope:
            scope.setattr(recovery_store.os, "replace", crash)
            with pytest.raises(OSError):
                RecoveryBackup(runner.store, backup.job_id).restore()
    restored = RecoveryBackup(runner.store, backup.job_id).restore()
    assert (root / "a").read_text() == "A1" and (root / "b").read_text() == "A2"
    assert restored["progress"]["recovery"]["id"] == backup.job_id


def test_business_before_images_are_scoped_and_conflicting_writes_are_not_overwritten(runner):
    task = task_at(runner)
    db = runner.knowledge._connect()
    with db:
        db.execute("INSERT INTO repair_reviews VALUES (?,?,?,?,?)", (task["state"]["task_id"], "rejected", "old", "{}", "before"))
        db.execute("INSERT INTO repair_reviews VALUES (?,?,?,?,?)", ("other-task", "rejected", "unrelated", "{}", "before"))
    db.close()
    backup = RecoveryBackup.capture(runner.store, task)
    before = backup.snapshot["business"]["repair_reviews"]
    after = {**before, "reason": "recovery modification"}
    backup.write_business("repair_reviews", after)
    backup.restore()
    assert RecoveryBackup.read_business(runner.knowledge.path, task["state"]["task_id"])["repair_reviews"] == before
    backup.write_business("repair_reviews", after)
    with sqlite3.connect(runner.knowledge.path) as db:
        db.execute("UPDATE repair_reviews SET reason='external update' WHERE task_id=?", (task["state"]["task_id"],))
    with pytest.raises(Fault, match="其他修改"):
        backup.restore()
    with sqlite3.connect(runner.knowledge.path) as db:
        assert db.execute("SELECT reason FROM repair_reviews WHERE task_id='other-task'").fetchone()[0] == "unrelated"
    with pytest.raises(Fault, match="当前任务"):
        backup.write_business("repair_reviews", {**after, "task_id": "other-task"})


def test_corrupt_backup_never_overwrites_live_files(runner):
    task = task_at(runner)
    root = Path(task["config"]["workspace"]).parent
    (root / "a").write_text("A")
    backup = RecoveryBackup.capture(runner.store, task)
    (root / "a").write_text("live")
    (backup.directory / "files" / "a").write_text("corrupt")
    with pytest.raises(Fault, match="损坏"):
        backup.restore()
    assert (root / "a").read_text() == "live"


def test_incomplete_capture_is_not_used_as_a(runner, monkeypatch):
    task = task_at(runner)
    root = Path(task["config"]["workspace"]).parent
    (root / "a").write_text("A")
    def denied(*args):
        raise OSError("disk full")
    monkeypatch.setattr("code_repair.recovery_store.durable_copy", denied)
    with pytest.raises(OSError):
        RecoveryBackup.capture(runner.store, task)
    with runner.store.connect() as db:
        job = db.execute("SELECT id,phase FROM recovery_jobs").fetchone()
    assert job[1] == "capturing"
    with pytest.raises(Fault):
        RecoveryBackup(runner.store, job[0]).restore()
    assert (root / "a").read_text() == "A"


def test_apply_interruption_rebuilds_deterministically_and_keeps_a(runner):
    task = task_at(runner, workspace=True, node="apply_patch", status="repairing")
    task, token = in_flight(runner, task, "apply_patch")
    path = Path(task["config"]["workspace"]) / "target.py"
    path.write_text("half written patch\n")
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["execution"] == "queued", saved["state"]
    assert saved["progress"]["next_node"] == "validate_patch"
    assert path.read_text() == "value = 2\n"
    backup = RecoveryBackup(runner.store, saved["progress"]["recovery"]["id"])
    assert (backup.directory / "files" / "workspace" / "target.py").read_text() == "half written patch\n"
    assert token in saved["progress"]["revoked_runs"]


def test_recovery_crash_restores_same_a_before_another_attempt(runner, monkeypatch):
    task = task_at(runner, workspace=True, node="apply_patch", status="repairing")
    task, _ = in_flight(runner, task, "apply_patch")
    path = Path(task["config"]["workspace"]) / "target.py"
    path.write_text("partial original\n")
    recovery = manager(runner)
    original = recovery._verify_and_repair
    def crash(task, backup):
        path.write_text("partial recovery\n")
        raise KeyboardInterrupt("simulated server death")
    with monkeypatch.context() as scope:
        scope.setattr(recovery, "_verify_and_repair", crash)
        with pytest.raises(KeyboardInterrupt):
            recovery.recover_pending()
    with runner.store.connect() as db:
        job_id = db.execute("SELECT id FROM recovery_jobs").fetchone()[0]
    def check_original(task, backup):
        assert path.read_text() == "partial original\n"
        assert backup.job_id == job_id
        return original(task, backup)
    monkeypatch.setattr(recovery, "_verify_and_repair", check_original)
    recovery.recover_pending()
    assert path.read_text() == "value = 2\n"
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM recovery_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("node", ["baseline_reproduction", "validate_patch", "propose_patch"])
def test_uncertain_code_or_model_is_paused_and_workspace_preserved(runner, node):
    task = task_at(runner, workspace=True, node=node)
    task, _ = in_flight(runner, task, node)
    task["progress"]["in_flight"]["code_execution_started"] = True
    task["progress"]["in_flight"].pop("model_tool_policy", None)
    runner.store.put(task)
    path = Path(task["config"]["workspace"]) / "target.py"
    path.write_text("unknown operation state\n")
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["execution"] == "idle" and saved["state"]["status"] == "needs_input"
    assert saved["progress"]["recovery"]["status"] == "paused"
    assert path.read_text() == "unknown operation state\n"


def test_boundary_resume_skips_completed_model_nodes_and_keeps_pending_review(runner, monkeypatch):
    task = task_at(runner, workspace=True, node="review_patch", status="reviewable")
    task["state"]["validation"] = {"passed": True, "patch_hash": task["state"]["patch_hash"], "summary": "ok", "report_path": ""}
    task["progress"].update(committed_state=deepcopy(task["state"]), state_hash=__import__("code_repair.resilience", fromlist=["*"]).digest(task["state"]), interrupted=True)
    runner.store.put(task)
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["execution"] == "queued", saved
    def forbidden(state):
        pytest.fail("completed node was replayed")
    monkeypatch.setattr(runner, "services", lambda task: RepairServices(forbidden, forbidden, forbidden, forbidden, forbidden, forbidden))
    runner.run(saved["state"]["task_id"], False)
    result = runner.store.get(saved["state"]["task_id"])
    assert result["execution"] == "idle" and result["state"]["status"] == "reviewable"
    assert result["review"] is None and result["state"]["review_decision"] == "pending"


def accepted_task(runner):
    task = task_at(runner, workspace=True, node="save_case", status="reviewable")
    state = task["state"]
    state.update(review_decision="accepted", validation={"passed": True, "patch_hash": state["patch_hash"], "summary": "ok", "report_path": ""})
    task["review"] = {"decision": "accepted", "patch_hash": state["patch_hash"], "reason": "", "created_at": timestamp()}
    runner.store.put(task)
    return in_flight(runner, task, "save_case")[0]


def test_business_commit_before_node_commit_reconciles_receipt_without_embedding_again(runner):
    task = accepted_task(runner)
    with business_effect(effect_key(task, "save_case"), task["state"]["task_id"], "save_case", effect_parameters(task, "save_case")):
        runner.knowledge.save_case(task["state"])
    assert probe_business(task, "save_case") == "completed"
    runner.knowledge = KnowledgeBase(runner.knowledge.path, embedder=no_agent)
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["status"] == "succeeded", saved["state"]
    assert saved["state"]["memory_saved"] and saved["execution"] == "idle"


def test_business_parameter_conflict_cannot_be_overwritten_by_agent(runner):
    task = accepted_task(runner)
    with business_effect(effect_key(task, "save_case"), task["state"]["task_id"], "save_case", effect_parameters(task, "save_case")):
        runner.knowledge.save_case(task["state"])
    with sqlite3.connect(runner.knowledge.path) as db:
        db.execute("UPDATE repair_cases SET document='{}' WHERE case_id=?", (task["state"]["task_id"],))
    assert probe_business(task, "save_case") == "conflict"
    agent = RecoveryAgent(lambda prompt: json.dumps({"action": "rebuild_workspace", "evidence": ["business"], "reason": "try overwriting"}))
    RecoveryManager(runner, agent).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["error_info"]["code"] == "recovery_action_denied"
    with sqlite3.connect(runner.knowledge.path) as db:
        assert db.execute("SELECT document FROM repair_cases WHERE case_id=?", (task["state"]["task_id"],)).fetchone()[0] == "{}"


def test_lost_usage_keeps_reservation_across_restore(runner):
    task = task_at(runner, workspace=True, node="propose_patch", remaining_tokens=20_000)
    task, _ = in_flight(runner, task, "propose_patch")
    task["progress"]["pending_calls"] = [
        {"id": "call-1", "reserved_tokens": 900, "reserved_cost": 0.03},
        {"id": "call-2", "reserved_tokens": 300, "reserved_cost": 0.01},
    ]
    runner.store.put(task)
    recovery = manager(runner)
    recovery.recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["remaining_budget"]["tokens"] == 18800
    assert saved["state"]["remaining_budget"]["cost"] == pytest.approx(0.06)
    assert saved["state"]["error_info"]["code"] == "usage_unknown"
    assert all(call["reservation_charged"] for call in saved["progress"]["pending_calls"])

    recovery.recover(saved["state"]["task_id"], saved["progress"]["recovery"]["id"])
    retried = runner.store.get(task["state"]["task_id"])
    assert retried["state"]["remaining_budget"]["tokens"] == 18800
    assert retried["state"]["remaining_budget"]["cost"] == pytest.approx(0.06)
    assert all(call["reservation_charged"] for call in retried["progress"]["pending_calls"])


def test_lost_node_commit_ack_does_not_repeat_effect(runner, monkeypatch):
    task = task_at(runner, node="collect_context")
    task, token = in_flight(runner, task, "collect_context")
    original = runner.store.put
    def lost_ack(value):
        original(value)
        raise OSError("lost commit acknowledgement")
    monkeypatch.setattr(runner.store, "put", lost_ack)
    runner.store.finish_step(task, "collect_context", task["state"], {"status": "repairing"}, "propose_patch", token)
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["progress"]["in_flight"] is None and saved["progress"]["next_node"] == "propose_patch"


def test_agent_schema_and_permission_allowlist():
    agent = RecoveryAgent(lambda prompt: '{"action":"run_shell","evidence":["workspace"],"reason":"execute"}')
    with pytest.raises(Fault, match="校验"):
        agent.advise({}, {"pause"})


def test_single_service_lock(runner):
    lock = ServiceLock(runner.store.root)
    lock.acquire()
    try:
        with pytest.raises(Fault, match="已有服务"):
            ServiceLock(runner.store.root).acquire()
    finally:
        lock.release()
    other = ServiceLock(runner.store.root)
    other.acquire()
    other.release()


def test_persisted_queue_is_dispatched_after_startup_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    runner.worker_mode = "inline"
    task = task_at(runner, node="finish")
    with TestClient(app) as client:
        import time
        for _ in range(100):
            saved = client.get('/api/tasks/' + task['state']['task_id']).json()
            if saved["execution"] == "idle":
                break
            time.sleep(0.02)
        assert saved["execution"] == "idle", saved


def test_long_nested_checkpoint_files_survive_copy_and_restore(runner):
    task = task_at(runner)
    root = filesystem_path(Path(task["config"]["workspace"]).parent)
    nested = root.joinpath(*(["nested_reports"] * 17), "original.txt")
    nested.parent.mkdir(parents=True)
    nested.write_text("before recovery", encoding="utf-8")
    assert len(str(nested)) > 260
    backup = RecoveryBackup.capture(runner.store, task)
    nested.write_text("partial recovery", encoding="utf-8")
    backup.restore()
    assert nested.read_text(encoding="utf-8") == "before recovery"


def test_original_paths_cannot_collide_with_restore_temporary_files(runner):
    task = task_at(runner)
    root = Path(task["config"]["workspace"]).parent
    (root / "original").write_text("before")
    (root / "original.recovery-tmp").mkdir()
    (root / "original.recovery-tmp" / "payload").write_text("keep")
    backup = RecoveryBackup.capture(runner.store, task)
    (root / "original").write_text("partial recovery")
    backup.restore()
    assert (root / "original").read_text() == "before"
    assert (root / "original.recovery-tmp" / "payload").read_text() == "keep"


def test_interrupted_business_recovery_restores_original_a_before_retry(runner, monkeypatch):
    task = task_at(runner, workspace=True, node="apply_patch")
    task, _ = in_flight(runner, task, "apply_patch")
    with runner.knowledge._connect() as db:
        db.execute("INSERT INTO repair_reviews VALUES (?,?,?,?,?)", (task["state"]["task_id"], "rejected", "original", "{}", "before"))
    recovery = manager(runner)
    original_verify = recovery._verify_and_repair
    def crash(task, backup):
        before = backup.snapshot["business"]["repair_reviews"]
        backup.write_business("repair_reviews", {**before, "reason": "partial recovery"})
        (Path(task["config"]["workspace"]) / "target.py").write_text("partial recovery")
        raise KeyboardInterrupt("process terminated during recovery")
    with monkeypatch.context() as scope:
        scope.setattr(recovery, "_verify_and_repair", crash)
        with pytest.raises(KeyboardInterrupt):
            recovery.recover_pending()
    def verify_restored_a(task, backup):
        business = RecoveryBackup.read_business(runner.knowledge.path, task["state"]["task_id"])
        assert business["repair_reviews"]["reason"] == "original"
        assert (Path(task["config"]["workspace"]) / "target.py").read_text() == "value = 1\n"
        return original_verify(task, backup)
    monkeypatch.setattr(recovery, "_verify_and_repair", verify_restored_a)
    recovery.recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["progress"]["next_node"] == "validate_patch"
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM recovery_jobs").fetchone()[0] == 1


def test_committed_recovery_is_not_rolled_back_when_acknowledgement_is_lost(runner, monkeypatch):
    task = task_at(runner, workspace=True, node="apply_patch")
    task, _ = in_flight(runner, task, "apply_patch")
    recovery = manager(runner)
    original_trace = recovery._trace
    def lose_ack(task_id, event, **kwargs):
        if event == "committed":
            raise OSError("lost commit acknowledgement")
        return original_trace(task_id, event, **kwargs)
    monkeypatch.setattr(recovery, "_trace", lose_ack)
    recovery.recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["progress"]["recovery"]["status"] == "resumed"
    assert saved["progress"]["next_node"] == "validate_patch"
    assert (Path(task["config"]["workspace"]) / "target.py").read_text() == "value = 2\n"


def test_recovery_rejects_foreign_business_intent_without_changing_it(runner):
    task = accepted_task(runner)
    key = effect_key(task, "save_case")
    runner.durable_operations.begin(key, "another-task", "save_case", effect_parameters(task, "save_case"))
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["error_info"]["code"] == "idempotency_conflict"
    assert runner.durable_operations.lookup(key)["status"] == "started"


def test_recovery_agent_can_retry_failed_evidence_query_but_never_assert_success(runner, monkeypatch):
    task = accepted_task(runner)
    with business_effect(effect_key(task, "save_case"), task["state"]["task_id"], "save_case", effect_parameters(task, "save_case")):
        runner.knowledge.save_case(task["state"])
    calls = []
    original = probe_business
    def transient_query(task, name):
        calls.append(name)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database locked")
        return original(task, name)
    monkeypatch.setattr("code_repair.recovery_manager.probe_business", transient_query)
    advice = []
    def advise(prompt):
        advice.append(prompt)
        return json.dumps({"action": "retry_query", "evidence": ["business"], "reason": "retry bounded query"})
    RecoveryManager(runner, RecoveryAgent(advise)).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["status"] == "succeeded"
    assert len(calls) == 2 and len(advice) == 1
    assert str(runner.store.root) not in advice[0]


def test_service_startup_recovers_partial_patch_and_dispatches_only_remaining_nodes(runner, monkeypatch):
    task = task_at(runner, workspace=True, node="apply_patch", status="repairing")
    task, _ = in_flight(runner, task, "apply_patch")
    target = Path(task["config"]["workspace"]) / "target.py"
    target.write_text("half applied before service stopped\n")
    restarted = create_app(runner.store.root)
    restarted.state.runner.worker_mode = "inline"
    def forbidden(state):
        pytest.fail("a completed node was replayed after restart")
    validated = []
    def validate(state):
        assert target.read_text() == "value = 2\n"
        validated.append(state["patch_hash"])
        return {"passed": True, "patch_hash": state["patch_hash"], "summary": "ok", "report_path": ""}
    monkeypatch.setattr(restarted.state.runner, "services", lambda task: RepairServices(
        forbidden, forbidden, forbidden, forbidden, validate, forbidden,
        adversarial_review=lambda _: {"approved": True, "summary": "断点测试显式审查桩", "findings": []},
    ))
    with TestClient(restarted) as client:
        from tests.test_repair_api import finished
        saved = finished(client, task["state"]["task_id"])
        assert saved["state"]["status"] == "reviewable"
        assert saved["progress"]["recovery"]["status"] == "resumed"
        assert saved["progress"]["next_node"] == "finish"
        assert len(validated) == 1 and saved["review"] is None


@pytest.mark.parametrize("node", ["baseline_reproduction", "validate_patch"])
def test_unstarted_code_can_resume_when_usage_is_known(runner, node):
    task = task_at(runner, workspace=True, node=node)
    task, _ = in_flight(runner, task, node)
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["execution"] == "queued", saved["state"]
    assert saved["progress"]["next_node"] == node
    assert saved["progress"]["in_flight"] is None


def test_interrupted_model_tool_node_is_not_replayed_after_side_effects_are_enabled(runner):
    task = task_at(runner, workspace=True, node="propose_patch")
    task, _ = in_flight(runner, task, "propose_patch")
    assert task["progress"]["in_flight"]["model_tool_policy"] == "side_effecting"

    manager(runner).recover_pending()

    saved = runner.store.get(task["state"]["task_id"])
    assert saved["execution"] == "idle"
    assert saved["progress"]["recovery"]["status"] == "paused"
    assert saved["state"]["status"] == "needs_input"
    assert saved["state"]["error_info"]["code"] == "model_outcome_unknown"
