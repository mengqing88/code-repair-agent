"""Opt-in real Docker Engine acceptance. No mocked docker CLI or execution results."""
from __future__ import annotations

import argparse
from dataclasses import replace
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from uuid import uuid4

from code_repair.docker_executor import BindMount, cleanup_docker_task, run_execution
from code_repair.execution_manifest import freeze, task_configuration
from code_repair.failure_regression import FailureRegressions, review_content_hash
from code_repair.knowledge_base import KnowledgeBase
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, create_checkpoint_workspace, remove_checkpoint_workspace
from code_repair.repair_workflow import build_repair_workflow
from code_repair.reproduction import execute_reproduction
from code_repair.resilience import cleanup_scope, digest, execution_scope


def acceptance(root, report):
    info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                          capture_output=True, text=True, timeout=15)
    if info.returncode:
        report.update(status="blocked", reason="Docker Engine 无法连接", diagnostic=info.stderr[-1500:])
        return
    report["engine_version"] = info.stdout.strip()
    source = root / "source"
    source.mkdir()
    original = "def add(a, b):\n    return a - b\n"
    fixed = original.replace("a - b", "a + b")
    (source / "bug.py").write_text(original, encoding="utf-8")
    (source / "test_bug.py").write_text("from bug import add\ndef test_add():\n    assert add(2, 1) == 3\n", encoding="utf-8")
    directory = root / "task"
    directory.mkdir()
    workspace = directory / "workspace"
    task_id = uuid4().hex
    checkpoint = create_checkpoint_workspace(source, workspace)
    knowledge = KnowledgeBase(root / "knowledge.sqlite3", embedder=lambda _: [1.0, 0.0])
    state = create_initial_state(task_id, {"type": "python_file", "path": str(source / "bug.py")},
                                 "add(2,1) should return 3, currently returns 1", remaining_seconds=300)
    config = {"test_targets": ["test_bug.py"], "allowed_paths": ["bug.py"],
              "source_fingerprint": {}, "execution_backend": "docker", "deployment_mode": "untrusted",
              "language": "python"}
    task = {"state": state, "config": config}
    manifest, _ = freeze(task, directory, {"report": state["original_report"]}, knowledge)
    report["image"] = manifest["program"]["container"]
    patch_text = "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                             fromfile="a/bug.py", tofile="b/bug.py"))
    try:
        with task_configuration(task, directory), execution_scope(300, task_id=task_id,
                trace_path=directory / "trace.jsonl", max_tokens=20_000, max_cost=1):
            toolbox = RepairToolbox(workspace_root=workspace, checkpoint_revision=checkpoint,
                source_root=source,
                allowed_paths=("bug.py",), test_targets=("test_bug.py",), execution_backend="docker",
                knowledge_base=knowledge, task_id=task_id, validation_report_path=directory / "validation.md")
            def baseline(value):
                (directory / "reproduction").mkdir(parents=True, exist_ok=True)
                output = execute_reproduction("from bug import add\nprint(add(2,1))", workspace,
                                              directory / "reproduction", 30, execution_backend="docker", task_id=task_id)
                return {"matched": output["returncode"] == 0 and output["stdout"].strip() == "1",
                        "record": {"reproduction_code": "from bug import add\nprint(add(2,1))", "execution": output}}
            def review_model(prompt):
                return json.dumps({"summary": "隔离验收使用固定模型样本", "checks": [
                    {"aspect": aspect, "verdict": "pass", "path": "bug.py", "quote": "return a + b",
                     "reasoning": "固定模型样本仅隔离外部模型服务，实际源码与测试执行来自真实容器"}
                    for aspect in ("boundary_inputs", "behavior_compatibility", "patch_risk")]})
            with patch("code_repair.defect_regression.ask_model_with_usage", return_value=json.dumps({
                "test_body": "from bug import add\nassert add(2,1) == 3", "clarification_required": False,
                "clarification_reasons": [], "question": ""})), patch("code_repair.adversarial_review.ask_model_with_usage", review_model):
                services = toolbox.as_repair_services(baseline, propose_patch=lambda _: {
                    "diagnosis": "将减法更正为加法", "patch_diff": patch_text,
                    "patch_hash": hashlib.sha256(patch_text.encode()).hexdigest()})
                result = build_repair_workflow(services).invoke(state)
            assert result["status"] == "reviewable", result.get("last_error")
            report["repair"] = {"status": result["status"], "validation": result["validation"],
                                "source_unchanged": (source / "bug.py").read_text() == original}
            assert report["repair"]["source_unchanged"]
            (workspace / ".env").write_text("PRIVATE_TEST_VALUE=do-not-expose", encoding="utf-8")
            guard = workspace / "test_bug.py"
            script = (
                "import pathlib, socket\n"
                "assert 'do-not-expose' not in pathlib.Path('.env').read_text()\n"
                "assert not pathlib.Path('.git').read_text() if pathlib.Path('.git').is_file() else not list(pathlib.Path('.git').iterdir())\n"
                "assert len(socket.if_nameindex()) == 1\n"
                "assert int(next(line.split(':')[1] for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')),16) == 0\n"
                "for target in ('/etc/repair-probe', 'test_bug.py'):\n"
                "    try: pathlib.Path(target).write_text('changed')\n"
                "    except OSError: pass\n"
                "    else: raise AssertionError('protected mount writable: '+target)\n"
                "print('mounts-and-limits-ok')\n"
            )
            output = run_execution(["python", "-c", script], workspace=workspace, timeout=30, backend="docker",
                task_id=task_id, extra_mounts=(BindMount(guard, "/workspace/test_bug.py", True),))
            assert output.returncode == 0, output.stderr
            report["mount_protection"] = {"passed": True, "stdout": output.stdout}
            try:
                run_execution(["python", "-c", "import time; time.sleep(60)"], workspace=workspace,
                              timeout=2, backend="docker", task_id=task_id)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise AssertionError("超时未触发")
            assert cleanup_docker_task(task_id)
            report["timeout_cleanup"] = True
            # This feedback checks behavior preserved by the buggy baseline, broken by a constant patch.
            rejected = dict(result, review_reason="add(-2,0)应为-2，固定返回3破坏了原有行为")
            rejected["patch_diff"] = patch_text.replace("+    return a + b", "+    return 3")
            rejected["patch_hash"] = hashlib.sha256(rejected["patch_diff"].encode()).hexdigest()
            archive = FailureRegressions(knowledge.path)
            candidate = archive.process(rejected, toolbox, lambda _: json.dumps({
                "classification": "behavior_regression", "uncertain": False,
                "rationale": "输入 b=0 时基线保留正确结果，固定返回值破坏该路径",
                "test_body": "from bug import add\nassert add(-2,0) == -2"}))
            assert candidate["status"] == "verified", candidate.get("reason")
            archive.decide(candidate["id"], "approve", "验收夹具人工审批模拟", review_content_hash(candidate), "acceptance-fixture")
            validation = toolbox.validate_patch_for_state(result)
            assert validation["passed"] and len(validation["feedback_regressions"]) == 1
            report["feedback_loop"] = candidate["validation"]
            report["status"] = "passed"
    finally:
        with cleanup_scope():
            report["final_cleanup"] = False
            report["workspace_cleanup"] = False
            report["container_cleanup"] = cleanup_docker_task(task_id)
            if not report["container_cleanup"]:
                raise RuntimeError("无法确认验收容器已清理")
            remove_checkpoint_workspace(workspace, checkpoint)
            report["workspace_cleanup"] = True
            report["final_cleanup"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "failed", "scope": "real Docker; fixed model responses; synthetic defect"}
    try:
        with tempfile.TemporaryDirectory(prefix="repair-docker-acceptance-") as temp:
            acceptance(Path(temp), report)
    except Exception as error:
        report.update(status="failed", reason=f"{type(error).__name__}: {error}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
