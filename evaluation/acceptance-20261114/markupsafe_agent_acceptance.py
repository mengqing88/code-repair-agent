"""Run one public MarkupSafe defect through the real task API and Docker workflow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evaluation" / "public-defects-v2-20261112" / "markupsafe-417"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(source), *arguments], check=True,
                            capture_output=True, text=True, timeout=30)
    return result.stdout.strip()


def score_saved_candidate(output: Path) -> int:
    """Independently test an already validated candidate without issuing model requests."""
    from docker_executor import cleanup_docker_task, run_execution
    from repair_api import TaskStore
    from repair_tools import apply_patch

    report_path = output / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    task_id = report.get("task_id")
    if not task_id:
        raise ValueError("Existing acceptance report has no task_id")
    task = TaskStore(output / "data").get(task_id)
    state = task["state"]
    patch = state.get("patch_diff") or ""
    expected_hash = (report.get("agent") or {}).get("patch_sha256")
    observed_hash = hashlib.sha256(patch.encode()).hexdigest() if patch else None
    if (not patch or observed_hash != expected_hash
            or (report.get("agent", {}).get("validation") or {}).get("passed") is not True):
        raise ValueError("Persisted candidate does not match the recorded successful internal validation")

    source = output / "source"
    score_id = uuid4().hex[:8]
    independent_root = output / f"independent-candidate-{score_id}"
    score_report = output / f"candidate-independent-{score_id}.json"
    shutil.copytree(source, independent_root)
    evidence = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "independent Docker regression of a saved model candidate; no model or MCP requests",
        "task_id": task_id,
        "workflow_status": state.get("status"),
        "workflow_error_code": (state.get("error_info") or {}).get("code"),
        "patch_sha256": observed_hash,
        "baseline_commit": git(independent_root, "rev-parse", "HEAD"),
        "patch_applied": False,
        "test": None,
    }
    container_task_id = "public417-score-" + uuid4().hex[:16]
    try:
        applied = apply_patch(
            patch, workspace_root=independent_root,
            allowed_paths=("markupsafe/__init__.py",),
            protected_test_targets=("test_markupsafe_417.py",),
            protected_test_files=("test_markupsafe_417.py",),
        )
        evidence["patch_applied"] = True
        evidence["patch_paths"] = applied["paths"]
        observed = run_execution(
            ["python", "-B", "-m", "pytest", "-q", "-c", "/dev/null", "-p",
             "no:cacheprovider", "test_markupsafe_417.py"],
            workspace=independent_root, timeout=60, backend="docker", task_id=container_task_id,
        )
        evidence["test"] = {"return_code": observed.returncode, "passed": observed.returncode == 0,
                            "stdout": observed.stdout[-4000:], "stderr": observed.stderr[-4000:],
                            "container_cleaned": cleanup_docker_task(container_task_id)}
    finally:
        if evidence["test"] is None:
            evidence["test"] = {"passed": False, "container_cleaned": cleanup_docker_task(container_task_id)}
        evidence["status"] = "passed" if (evidence["patch_applied"]
            and evidence["test"].get("passed") and evidence["test"].get("container_cleaned")) else "failed"
        (score_report).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--score-saved-candidate", action="store_true",
                        help="independently run Docker tests for a persisted candidate without model calls")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.score_saved_candidate:
        return score_saved_candidate(output)
    if output.exists():
        raise SystemExit(f"Refusing to overwrite acceptance output: {output}")
    output.mkdir(parents=True)
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "scope": "one BSD-3-Clause MarkupSafe issue 417, real configured model, production task workflow and Docker, independent candidate test",
        "evidence": {"issue": 417, "upstream_base": None, "upstream_fix": None,
                     "source_snapshot_hashes_verified": False},
        "limitations": [
            "One authored minimal regression test from one library; no upstream full suite, C extension, or Python matrix",
            "One task does not estimate production repair rate or RAG uplift",
            "Candidate is not human approved or published",
        ],
    }
    task_id = None
    task_root = output / "data"
    source = output / "source"
    independent_id = "public417-" + uuid4().hex[:20]
    try:
        from resilience import error_info
        from repair_api import create_app
        from docker_executor import cleanup_docker_task, run_execution
        from repair_tools import apply_patch

        provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
        recorded = provenance.get("observed_sha256", {})
        verified = True
        for relative, expected in recorded.items():
            fixture_file = FIXTURE / relative
            if not fixture_file.is_file() or sha(fixture_file) != expected:
                verified = False
                raise ValueError("Public defect fixture hash mismatch")
        if not provenance.get("passed") or provenance.get("license") != "BSD-3-Clause":
            raise ValueError("Public defect provenance or license check failed")

        source.mkdir()
        shutil.copytree(FIXTURE / "before" / "markupsafe", source / "markupsafe")
        shutil.copyfile(FIXTURE / "before" / "LICENSE.rst", source / "LICENSE.rst")
        (source / "test_markupsafe_417.py").write_text(
            "from markupsafe import Markup\n\n"
            "def test_whitespace_around_removed_comment_is_collapsed():\n"
            "    assert Markup('a <!-- hidden --> b').striptags() == 'a b'\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "--quiet", str(source)], check=True,
                       capture_output=True, timeout=30)
        git(source, "config", "user.name", "Local Acceptance")
        git(source, "config", "user.email", "acceptance@localhost")
        git(source, "add", "--all")
        git(source, "commit", "--quiet", "-m", "pin public MarkupSafe 417 baseline")
        revision = git(source, "rev-parse", "HEAD")
        report["evidence"].update(upstream_base=provenance["base"], upstream_fix=provenance["fixed"],
                                  source_snapshot_hashes_verified=verified,
                                  local_baseline_commit=revision)

        mcp_config = output / "empty-mcp.json"
        mcp_config.write_text('{"servers": []}', encoding="utf-8")
        os.environ.update({
            "REPAIR_DATA_DIR": str(task_root),
            "REPAIR_DEPLOYMENT_MODE": "local",
            "REPAIR_EXECUTION_MODE": "docker",
            "REPAIR_MAX_WORKERS": "1",
            "MCP_SERVERS_CONFIG": str(mcp_config),
        })
        app = create_app(task_root)
        username = "public417_" + uuid4().hex[:12]
        with TestClient(app, base_url="http://127.0.0.1") as client:
            registered = client.post("/api/auth/register", json={
                "username": username, "password": uuid4().hex + uuid4().hex,
            })
            if registered.status_code != 201:
                raise RuntimeError("Acceptance account registration failed")
            user = registered.json()["user"]
            client.headers.update({"X-Account-ID": user["id"],
                                   "X-CSRF-Token": registered.json()["csrf_token"]})
            created = client.post("/api/tasks", headers={"Idempotency-Key": "public417-" + uuid4().hex}, json={
                "source_type": "git_repository",
                "path": str(source),
                "revision": revision,
                "error_report": (
                    "MarkupSafe issue 417: removing an HTML comment can leave adjacent whitespace uncollapsed. "
                    "For Markup('a <!-- hidden --> b').striptags(), preserve the public behavior and return 'a b'. "
                    "Fix the implementation and verify the regression test."
                ),
                "test_target": "test_markupsafe_417.py",
                "language": "python",
                "test_command": "python -B -m pytest -q test_markupsafe_417.py",
                "reproduce_command": "python -B -m pytest -q test_markupsafe_417.py",
                "regression_command": "python -B -m pytest -q test_markupsafe_417.py",
                "runtime_seconds": 1200,
            })
            if created.status_code != 202:
                report["task_creation"] = {"status_code": created.status_code,
                                           "error_code": created.json().get("error", {}).get("code")}
                raise RuntimeError("Acceptance task creation failed")
            task_id = created.json()["state"]["task_id"]
            report["task_id"] = task_id
            deadline = time.monotonic() + 1260
            while time.monotonic() < deadline:
                response = client.get(f"/api/tasks/{task_id}")
                if response.status_code != 200:
                    raise RuntimeError("Acceptance task became unavailable")
                task = response.json()
                if task["execution"] == "idle" and task["state"]["status"] not in (
                        "queued", "running", "repairing"):
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Acceptance task exceeded its 1200-second runtime budget")

            state = task["state"]
            patch = state.get("patch_diff") or ""
            task_trace = task_root / "tasks" / task_id / "trace.jsonl"
            events = [json.loads(line) for line in task_trace.read_text(encoding="utf-8").splitlines() if line.strip()]
            report["agent"] = {
                "workflow_status": state.get("status"),
                "error_code": (state.get("error_info") or {}).get("code"),
                "candidate_present": bool(patch),
                "patch_sha256": hashlib.sha256(patch.encode()).hexdigest() if patch else None,
                "validation": state.get("validation"),
                "reproduction": state.get("reproduction"),
                "retrieved_cases": len(state.get("case_matches") or []),
                "tokens_observed": (state.get("accounting") or {}).get("tokens_observed"),
                "usage_complete": (state.get("accounting") or {}).get("usage_complete"),
                "elapsed_seconds": state.get("elapsed_seconds"),
                "trace_event_count": len(events),
                "trace_operations": sorted({event.get("operation", "") for event in events}),
                "trace_errors": sorted({event.get("error_code") for event in events if event.get("error_code")}),
                "review_decision": task.get("review", {}).get("decision") if task.get("review") else None,
            }
            report["evidence"]["source_unchanged"] = git(source, "status", "--porcelain") == ""
            if not report["evidence"]["source_unchanged"]:
                raise RuntimeError("Agent changed the pinned source repository instead of its task copy")

            if patch and state.get("validation", {}).get("passed") is True:
                shutil.copytree(source, output / "independent")
                apply_patch(
                    patch, workspace_root=output / "independent",
                    allowed_paths=("markupsafe/__init__.py",),
                    protected_test_targets=("test_markupsafe_417.py",),
                    protected_test_files=("test_markupsafe_417.py",),
                )
                independent = run_execution(
                    ["python", "-B", "-m", "pytest", "-q", "-c", "/dev/null", "-p",
                     "no:cacheprovider", "test_markupsafe_417.py"],
                    workspace=output / "independent", timeout=60,
                    backend="docker", task_id=independent_id,
                )
                report["independent_test"] = {
                    "return_code": independent.returncode,
                    "passed": independent.returncode == 0,
                    "stdout": independent.stdout[-4000:],
                    "stderr": independent.stderr[-4000:],
                    "container_cleaned": cleanup_docker_task(independent_id),
                }
            else:
                report["independent_test"] = {"passed": False, "skipped": True,
                                               "reason": "No internally validated candidate patch"}

        report["status"] = "passed" if (report.get("agent", {}).get("workflow_status") == "reviewable"
            and report.get("agent", {}).get("candidate_present")
            and report.get("independent_test", {}).get("passed")
            and report.get("independent_test", {}).get("container_cleaned")) else "failed"
    except Exception as error:
        from resilience import error_info
        report["status"] = "failed"
        report["failure"] = error_info(error, operation="public_defect.acceptance")
    finally:
        from docker_executor import cleanup_docker_task
        cleanup = {}
        if task_id:
            cleanup["agent_container_cleaned"] = cleanup_docker_task(task_id)
        cleanup["independent_container_cleaned"] = cleanup_docker_task(independent_id)
        report["cleanup"] = cleanup
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
