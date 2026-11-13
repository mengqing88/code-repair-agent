"""Contract alignment: real local persistence/Node, simulated model and GPU failures."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from execution_manifest import snapshot_knowledge
from knowledge_base import KnowledgeBase
from native_regression import native_test_code, run_native_regression, generate_native_regression
from qdrant_store import QdrantVectorIndex
from repair_api import TaskStore
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace
from repair_workflow import RepairServices, build_repair_workflow
from recovery_store import initial_progress
from resilience import Fault, execution_scope
from retrieval_reranker import CrossEncoderReranker
from test_retrieval_reranker import make_case
from workflow_checkpoint import task_checkpointer
from workflow_replay import recording_scope, replay_recording


def services(events):
    def call(name, value):
        def action(state):
            events.append(name)
            return value(state) if callable(value) else deepcopy(value)
        return action
    return RepairServices(
        call("reproduce", {"matched": True, "record": {"status": "matched"}}),
        call("context", {"code_evidence": [], "case_matches": []}),
        call("propose", {"diagnosis": "boundary bug", "patch_diff": "diff", "patch_hash": "hash"}),
        call("apply", None),
        call("validate", lambda s: {"passed": True, "patch_hash": s["patch_hash"], "summary": "ok", "report_path": "test"}),
        call("save", None), adversarial_review=call("review", {"approved": True, "summary": "model stub", "findings": []}))


def test_native_checkpoints_survive_reopen_and_reject_stale_epoch(tmp_path):
    store = TaskStore(tmp_path / "data")
    state = create_initial_state("checkpoint-test", {"type": "pasted_code"}, "boundary bug")
    task = {"state": state, "config": {}, "progress": initial_progress(), "execution": "queued"}
    store.put(task)
    task, run_id = store.claim(state["task_id"])
    directory = tmp_path / "record"
    directory.mkdir()
    callbacks = replace(services([]), reproduce=lambda _: {"matched": False, "record": {}})
    with task_checkpointer(directory, store, task) as (saver, config):
        graph = build_repair_workflow(callbacks, checkpointer=saver)
        graph.invoke(state, config=config, durability="sync")
        saved = saver.get_tuple(config)
        assert saved.checkpoint["channel_values"]["status"] == "needs_input"
        assert len(list(saver.list(config))) >= 2
        old_epoch = task["progress"]["execution_epoch"]
        store.revoke(task)
        next_task, _ = store.claim(state["task_id"])
        assert next_task["progress"]["execution_epoch"] > old_epoch
        with pytest.raises(Fault, match="旧执行"):
            saver.put(config, saved.checkpoint, saved.metadata, {})
        with pytest.raises(Fault, match="旧执行"):
            saver.put_writes(config, [("status", "succeeded")], "late-write")
    with sqlite3.connect(directory / "langgraph.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 2


def test_recorded_node_code_reexecutes_without_services(tmp_path, monkeypatch):
    calls = []
    state = create_initial_state("replay-test", {"type": "pasted_code"}, "boundary bug")
    with execution_scope(60, trace_path=tmp_path / "trace.jsonl"), recording_scope(tmp_path):
        result = build_repair_workflow(services(calls)).invoke(state)
    assert result["status"] == "reviewable"
    before = list(calls)
    monkeypatch.setattr("llm_client.ask_model_with_usage", lambda *_: pytest.fail("external model was called"))
    records = list((tmp_path / "node-recordings").glob("*.json"))
    assert len(records) == 6
    assert all(replay_recording(path)["status"] == "replayed" for path in records)
    assert calls == before
    record = json.loads(records[0].read_text(encoding="utf-8"))
    record["events"] = []
    records[0].write_text(json.dumps(record), encoding="utf-8")
    assert replay_recording(records[0])["status"] == "diverged"


def test_redacted_recordings_cannot_claim_determinism(tmp_path):
    state = create_initial_state("sensitive", {"type": "pasted_code"}, "API_KEY=sk-test-very-secret-value")
    callbacks = replace(services([]), reproduce=lambda _: {"matched": False, "record": {}})
    with recording_scope(tmp_path):
        build_repair_workflow(callbacks).invoke(state)
    path = next((tmp_path / "node-recordings").glob("*.json"))
    assert "sk-test-very-secret-value" not in path.read_text(encoding="utf-8")
    assert replay_recording(path)["status"] == "unavailable"


@pytest.mark.parametrize("fault", [ValueError("bad result"), Fault("offline", "service unavailable")])
def test_failed_service_diagnostics_also_replay(tmp_path, fault):
    def broken(_):
        raise fault
    state = create_initial_state("failed-replay", {"type": "pasted_code"}, "boundary bug")
    with recording_scope(tmp_path):
        result = build_repair_workflow(replace(services([]), reproduce=broken)).invoke(state)
    assert result["status"] == "failed"
    assert all(replay_recording(p)["status"] == "replayed"
               for p in (tmp_path / "node-recordings").glob("*.json"))


def test_replay_requires_recording_for_every_committed_graph_node(tmp_path, monkeypatch):
    import execution_replay
    calls = []
    state = create_initial_state("coverage", {"type": "pasted_code"}, "boundary bug")
    with execution_scope(60, trace_path=tmp_path / "trace.jsonl"), recording_scope(tmp_path):
        result = build_repair_workflow(services(calls)).invoke(state)
    path = next((tmp_path / "node-recordings").glob("*.json"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    events = [{"operation": "workflow.recording", "event": "committed", "result": {
        "path": path.relative_to(tmp_path).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
        {"operation": "checkpoint", "event": "committed", "event_id": "commit-1",
         "details": {"node": saved["node"]}, "result": {"state": result,
            "next_node": __import__("repair_workflow").next_node_after(saved["node"], result)}},
        {"operation": "checkpoint", "event": "committed", "event_id": "commit-2",
         "details": {"node": "review_patch"}, "result": {"state": result, "next_node": "finish"}}]
    (tmp_path / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    task = {"state": result, "config": {"execution_manifest_hash": "test"}}
    monkeypatch.setattr(execution_replay, "verify", lambda *args: {"task": {"initial_state": state}})
    replayed = execution_replay.replay(task, tmp_path)
    assert replayed["status"] == "incomplete"
    assert not replayed["deterministic_node_replay"]
    assert not replayed["recording_coverage_complete"]


def test_epoch_high_watermark_survives_old_progress_snapshot(tmp_path):
    store = TaskStore(tmp_path / "data")
    state = create_initial_state("epochs", {"type": "pasted_code"}, "boundary bug")
    store.put({"state": state, "config": {}, "progress": initial_progress(), "execution": "queued"})
    first, _ = store.claim("epochs")
    store.revoke(first)
    second, _ = store.claim("epochs")
    store.revoke(second)
    with store.connect() as db:
        old = store.get("epochs")
        old["progress"]["execution_epoch"] = 0
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(old), "epochs"))
    third, _ = store.claim("epochs")
    assert third["progress"]["execution_epoch"] > second["progress"]["execution_epoch"]


def test_experience_role_runs_as_independent_node_and_replays(tmp_path):
    from multi_agent import AgentRole, current_agent_role, tool_allowed
    calls = []
    def retrieve(state):
        assert current_agent_role() == AgentRole.EXPERIENCE_RETRIEVER
        assert tool_allowed("retrieve_repair_experience")
        assert not tool_allowed("apply_patch")
        calls.append("retrieve")
        return {"case_matches": [], "failure_matches": []}
    callbacks = replace(services(calls), retrieve_experience=retrieve)
    state = create_initial_state("retrieval-role", {"type": "pasted_code"}, "boundary bug")
    with recording_scope(tmp_path):
        result = build_repair_workflow(callbacks).invoke(state)
    assert result["status"] == "reviewable"
    assert calls.index("context") < calls.index("retrieve") < calls.index("propose")
    assert result["experience_retrieval_pending"] is False
    paths = list((tmp_path / "node-recordings").glob("*.json"))
    assert any(json.loads(p.read_text(encoding="utf-8"))["node"] == "retrieve_experience" for p in paths)
    assert all(replay_recording(path)["status"] == "replayed" for path in paths)


@pytest.mark.parametrize("gpu_stage", ["load", "predict"])
def test_gpu_fault_uses_cpu_eight_threads_once(monkeypatch, gpu_stage):
    import numpy as np
    calls = []
    def load(model):
        calls.append((model.provider, model.threads))
        if model.provider == "DmlExecutionProvider" and gpu_stage == "load":
            raise RuntimeError("GPU unavailable")
        def predict(*_):
            if model.provider == "DmlExecutionProvider":
                raise RuntimeError("GPU lost")
            return [np.array([[0.8]])]
        return SimpleNamespace(get_inputs=lambda: [], run=predict), lambda *a, **k: {}
    monkeypatch.setattr(CrossEncoderReranker, "_load_onnx", load)
    model = CrossEncoderReranker("fixed-weights", "onnx", "fixed.onnx", "DmlExecutionProvider")
    assert model.score("q", ["d"]) == [0.8]
    assert model.score("q", ["d"]) == [0.8]
    assert calls == [("DmlExecutionProvider", 8), ("CPUExecutionProvider", 8)]
    assert model.active_provider == "CPUExecutionProvider"


def test_language_hard_filter_applies_to_sqlite_qdrant_and_merges(tmp_path):
    from qdrant_client import QdrantClient
    index = QdrantVectorIndex(QdrantClient(":memory:"))
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedder=lambda _: [1., 0.],
                              qdrant_index=index, reranker=None)
    for language in ("python", "javascript"):
        case = make_case(language)
        case["source"]["retrieval_scope"] = {"language": language, "repository_id": "same-repo",
                                              "file_paths": ["target.txt"], "dependency_versions": {}}
        knowledge.save_case(case)
    assert knowledge.count() == 2
    assert [r["case_id"] for r in knowledge.search("issue", filters={"language": "js"})] == ["javascript"]
    hits = index.search(namespace=knowledge.qdrant_namespace, vector=[1., 0.], limit=10,
                        outcome="accepted", filters={"language": "javascript"})
    assert [r["case_id"] for r in hits] == ["javascript"]
    assert knowledge.search("issue", filters={"language": "go"}) == []


def test_rejected_store_migration_and_frozen_export_preserve_history(tmp_path):
    path = tmp_path / "knowledge.sqlite3"
    legacy = KnowledgeBase(path, embedder=lambda _: [1., 0.], qdrant_index=None, reranker=None, outcomes_separated=False)
    legacy.save_case(make_case("accepted"))
    rejected = make_case("rejected")
    rejected.update(review_decision="rejected", review_reason="boundary regression")
    legacy.record_rejection(rejected)
    knowledge = replace(legacy, outcomes_separated=True)
    assert knowledge.failure_count() == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM repair_failures").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM repair_reviews").fetchone()[0] == 1
    with sqlite3.connect(knowledge.rejection_path) as db:
        assert db.execute("SELECT count(*) FROM repair_failures").fetchone()[0] == 1
    snapshot = tmp_path / "snapshot.sqlite3"
    snapshot_knowledge(knowledge, snapshot)
    frozen = replace(legacy, path=snapshot, outcomes_separated=False)
    assert frozen.count() == frozen.failure_count() == 1
    assert knowledge.failure_count() == 1


def test_rejected_migration_conflict_leaves_both_original_rows(tmp_path):
    from knowledge_base import KnowledgeBaseError
    path = tmp_path / "knowledge.sqlite3"
    legacy = KnowledgeBase(path, embedder=lambda _: [1., 0.], qdrant_index=None, reranker=None,
                           outcomes_separated=False)
    state = make_case("conflict")
    state.update(review_decision="rejected", review_reason="regression")
    legacy.record_rejection(state)
    separated = replace(legacy, outcomes_separated=True)
    assert separated.failure_count() == 1
    with sqlite3.connect(separated.rejection_path) as rejected, sqlite3.connect(path) as main:
        row = rejected.execute("SELECT * FROM repair_failures").fetchone()
        main.execute("INSERT INTO repair_failures VALUES (?,?,?,?,?,?,?,?,?)", row)
        main.execute("UPDATE repair_failures SET revision=revision+1")
    with pytest.raises(KnowledgeBaseError, match="冲突"):
        separated.failure_count()
    with sqlite3.connect(separated.rejection_path) as rejected, sqlite3.connect(path) as main:
        assert main.execute("SELECT revision FROM repair_failures").fetchone()[0] == row[4] + 1
        assert rejected.execute("SELECT revision FROM repair_failures").fetchone()[0] == row[4]


def test_rejected_body_and_review_roll_back_as_one_transaction(tmp_path, monkeypatch):
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedder=lambda _: [1., 0.],
                              qdrant_index=None, reranker=None)
    state = make_case("interrupted")
    state.update(review_decision="rejected", review_reason="regression")
    def fail_receipt(connection, document, *, write=False):
        if write:
            raise RuntimeError("receipt failed after body insert")
    monkeypatch.setattr(KnowledgeBase, "_receipt", staticmethod(fail_receipt))
    with pytest.raises(RuntimeError, match="receipt failed"):
        knowledge.record_rejection(state)
    assert knowledge.failure_count() == 0
    with sqlite3.connect(knowledge.path) as db:
        assert db.execute("SELECT count(*) FROM repair_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("xml,return_code", [
    ("<testsuite/>", 0),
    ('<testsuite><testcase><skipped/></testcase></testsuite>', 0),
    ('<testsuite><testcase><error type="ImportError"/></testcase></testsuite>', 1),
    ('<testsuite><testcase><failure type="SyntaxError"/></testcase></testsuite>', 1),
])
def test_native_junit_does_not_count_empty_skip_or_runtime_errors(tmp_path, xml, return_code):
    from native_regression import _parse_junit
    report = tmp_path / "result.xml"
    report.write_text(xml, encoding="utf-8")
    assert not _parse_junit(report, return_code)["valid"]


@pytest.mark.parametrize("field,new_value", [("language", "javascript"),
    ("test_command", ["other-tests"]), ("reproduce_command", ["other-repro"]),
    ("regression_command", ["other-regression", "{test_file}", "{report_file}"])])
def test_language_execution_contract_is_frozen(tmp_path, field, new_value):
    from execution_manifest import freeze, verify
    directory = tmp_path / "task"
    directory.mkdir()
    state = create_initial_state("commands", {"type": "pasted_code"}, "boundary bug")
    task = {"state": state, "config": {"language": "python", "test_command": [],
        "reproduce_command": [], "regression_command": [], "test_targets": ["."],
        "allowed_paths": ["target.py"], "execution_backend": "host", "source_fingerprint": {},
        "user_memory_snapshot": []}}
    knowledge = KnowledgeBase(tmp_path / "live.sqlite3", embedder=lambda _: [1., 0.],
                              qdrant_index=None, reranker=None)
    manifest, _ = freeze(task, directory, {}, knowledge)
    assert "native_regression.py" in manifest["prompts"]["templates"]
    task["config"][field] = new_value
    with pytest.raises(Fault, match="冻结清单"):
        verify(task, directory)



@pytest.mark.skipif(shutil.which("node") is None, reason="requires local Node.js")
def test_javascript_real_differential_and_original_reproduction(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.js").write_text("module.exports = {value: 1};\n", encoding="utf-8")
    (source / "existing.test.cjs").write_text("require('node:test')('existing', () => {});\n", encoding="utf-8")
    (source / "reproduce.cjs").write_text("if (require('./target.js').value !== 2) process.exit(1);\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    revision = create_checkpoint_workspace(source, workspace)
    toolbox = RepairToolbox(workspace, allowed_paths=("target.js",), test_targets=("existing.test.cjs",),
        language="javascript", test_command=("node", "--test", "existing.test.cjs"),
        reproduce_command=("node", "reproduce.cjs"), checkpoint_revision=revision,
        execution_backend="host", validation_report_path=tmp_path / "report" / "validation.md")
    body = "const value = (await import(pathToFileURL(resolve('target.js')).href)).default.value; assert.equal(value, 2);"
    generated = generate_native_regression("javascript", "value should be 2", {}, model_call=lambda _: json.dumps({
        "test_body": body, "question": "", "clarification_required": False, "clarification_reasons": []}))
    monkeypatch.setattr("native_regression.generate_native_regression", lambda *args: generated)
    state = create_initial_state("js", {"type": "code_file", "language": "javascript"}, "value should be 2")
    record = {"reproduction_code": "", "execution": {"returncode": 1}}
    prepared = toolbox.prepare_regression_for_state(state, record)
    assert not prepared.get("question"), prepared["regression"]["baseline"]
    assert prepared["regression"]["baseline"]["valid"]
    patch = "--- a/target.js\n+++ b/target.js\n@@ -1 +1 @@\n-module.exports = {value: 1};\n+module.exports = {value: 2};\n"
    state.update(patch_diff=patch, patch_hash=hashlib.sha256(patch.encode()).hexdigest())
    toolbox.apply_patch(patch)
    validation = toolbox.validate_patch_for_state(state)
    assert validation["passed"] is True, validation
    assert validation["regression"]["patched"]["valid"]
    assert validation["reproduction"]["returncode"] == 0
    assert not list(workspace.glob(".repair-native-*"))
    bad = native_test_code("javascript", "assert.equal((await import('./missing-module.js')).value, 2);")
    assert run_native_regression(toolbox, bad, 5)["valid"] is False
