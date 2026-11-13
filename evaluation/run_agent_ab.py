"""Paired real Agent runs on authored fixtures; oracles are scorer-only inputs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from evaluation import run_benchmark as bench
from docker_executor import BindMount, cleanup_docker_task, run_execution
from execution_manifest import (SnapshotKnowledge, freeze, runtime_identity,
                                task_configuration, write_once, _settings_and_mcp, _image)
from input_protection import protect_input
from knowledge_base import KnowledgeBase, _case_search_text, build_case_document
from llm_client import _chat_configuration, embed_texts
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace, remove_checkpoint_workspace
from repair_workflow import build_repair_workflow
from reproduction import reproduce
from resilience import (cleanup_scope, digest, error_info, execution_scope, run_process,
                        trace, trace_workflow_end, current, Fault)


ROOT = Path(__file__).resolve().parents[1]
BUDGET = {"runtime_seconds": 1200, "tokens": 200000, "cost_yuan": 0.1, "repair_attempts": 10}


def stop_unknown_usage(event, request_id, operation):
    """评测请求用量未知时停止该任务，避免在未结算额度上继续自动重试。"""
    if event == "unknown":
        context = current()
        if context:
            context.usage_known = context.cost_known = False
            context.active_request = None
        raise Fault("evaluation_usage_unknown", "评测请求用量未知，保留该任务额度并停止后续请求",
                    category="budget")


def export_artifacts(output, arm, case_id, directory, patch):
    for kind in ("patches", "traces"):
        (output / arm / kind).mkdir(parents=True, exist_ok=True)
    (output / arm / "patches" / f"{case_id}.patch").write_text(patch, encoding="utf-8")
    shutil.copyfile(directory / "trace.jsonl", output / arm / "traces" / f"{case_id}.jsonl")


def configure(output, plan=None):
    os.environ.update(MCP_SERVERS_CONFIG=str(output / "empty-mcp.json"), REPAIR_EXECUTION_MODE="docker")
    if plan:
        os.environ["REPAIR_DOCKER_IMAGE"] = plan["image"]["image_id"]
        settings, _ = _settings_and_mcp()
        if settings != plan["settings"]:
            raise ValueError("Effective model/embedding/pricing/retrieval settings changed after registration")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(protect_input(value), ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_state(document):
    patch = document["patch_diff"]
    return {"task_id": document["experience_id"], "review_decision": "accepted",
            "original_report": document["original_report"], "diagnosis": document["diagnosis"],
            "patch_diff": patch, "patch_hash": bench.digest(patch),
            "source": {"project": "synthetic-evaluation-only", "provenance": document["provenance"],
                       "review": "synthetic_adapter_not_human_review"},
            "reproduction": {"steps": "Fixture executed by benchmark self-check"},
            "applicable_conditions": document["applicable_conditions"],
            "validation": {"passed": True, "patch_hash": bench.digest(patch),
                           "summary": "Fixture tests observed in this run; not a real accepted repair"}}


def prepare(output, case_ids=None, *, task_tokens=None):
    budget = dict(BUDGET)
    if task_tokens is not None:
        if isinstance(task_tokens, bool) or not isinstance(task_tokens, int) or not 1000 <= task_tokens <= BUDGET["tokens"]:
            raise ValueError("task_tokens 必须是 1000 到默认 Token 上限之间的整数")
        budget["tokens"] = task_tokens
    full_benchmark = json.loads((bench.ROOT / "benchmark.json").read_text(encoding="utf-8"))
    benchmark = select_benchmark(full_benchmark, case_ids)
    selected = [record["case_id"] for record in benchmark["cases"]]
    output.mkdir(parents=True, exist_ok=False)
    histories = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((bench.ROOT / "history_cases").glob("*.json"))]
    # These are benchmark-author fixtures; no model-generated code executes in this host self-check.
    with tempfile.TemporaryDirectory(prefix="rag-ab-selfcheck-") as tmp:
        work = Path(tmp)
        checks = [bench.check_task(r, work / r["case_id"]) for r in benchmark["cases"]]
        experience_checks = [bench.check_experience(d, work / d["experience_id"]) for d in histories]
    save(output / "self-check.json", {"tasks": checks, "histories": experience_checks})
    if not all(x["valid"] for x in checks + experience_checks):
        raise ValueError("Fixture self-check failed; no Agent calls started")
    states = [corpus_state(d) for d in histories]
    texts = [_case_search_text(json.loads(build_case_document(s))) for s in states]
    with execution_scope(120, task_id="ab-corpus-build", trace_path=output / "corpus-build.trace.jsonl",
                         max_tokens=10000, on_request=stop_unknown_usage) as context:
        vectors = embed_texts(texts, batch_size=8)
        save(output / "corpus-build-usage.json", {"tokens": context.tokens, "cost_known": context.cost_known,
                                                "estimated_cost": context.cost if context.cost_known else None})
    cached = dict(zip(texts, vectors))
    for arm in ("baseline", "rag"):
        knowledge = KnowledgeBase(output / f"{arm}.sqlite3", embedder=cached.__getitem__, qdrant_index=None)
        with knowledge._connect():
            pass
        if arm == "rag":
            for state in states:
                knowledge.save_case(state)
    save(output / "benchmark.json", benchmark)
    inputs = []
    for record in benchmark["cases"]:
        source = bench.dataset_path(record["repository"])
        task = json.loads((source / "task.json").read_text(encoding="utf-8"))
        directory = output / "inputs" / record["case_id"]
        directory.mkdir(parents=True)
        for name in ("target.py", "test_existing.py"):
            shutil.copyfile(source / name, directory / name)
        save(directory / "task.json", task)
        inputs.append({"case_id": record["case_id"], "files": {p.name: sha(p) for p in directory.iterdir()}})
    settings, _, _, model = _chat_configuration(None)
    from runtime_config import setting
    empty_mcp = output / "empty-mcp.json"
    save(empty_mcp, {"servers": []})
    configure(output)
    from docker_executor import DEFAULT_IMAGE
    image = _image(setting("REPAIR_DOCKER_IMAGE", settings) or DEFAULT_IMAGE, "docker")
    if not image["image_id"]:
        raise ValueError("Real Docker image identity unavailable")
    os.environ["REPAIR_DOCKER_IMAGE"] = image["image_id"]
    frozen_settings, _ = _settings_and_mcp()
    runtime = runtime_identity()
    common = {"model": model, "prompt_sha256": digest(runtime["code"]), "budget": budget,
              "case_ids": selected}
    schedule = [(record["case_id"], arm) for record in benchmark["cases"] for arm in ("baseline", "rag")]
    random.Random(20261112).shuffle(schedule)
    plan = {"experiment_id": output.name, **common,
            "dataset_sha256": bench.digest(json.dumps(benchmark, ensure_ascii=False, sort_keys=True)),
            "full_dataset_sha256": bench.digest((bench.ROOT / "benchmark.json").read_text(encoding="utf-8")),
            "runtime": runtime, "runner_sha256": sha(Path(__file__)), "inputs": inputs,
            "settings": frozen_settings, "image": image,
            "corpus_sha256": sha(output / "rag.sqlite3"), "empty_corpus_sha256": sha(output / "baseline.sqlite3"),
            "embedding_model": setting("EMBEDDING_MODEL", settings),
            "history_files": {p.name: sha(p) for p in sorted((bench.ROOT / "history_cases").glob("*.json"))},
            "repeats": 1, "concurrency": 2, "schedule": schedule,
            "primary_outcome": "workflow reviewable AND independent 3 existing + 3 hidden tests pass",
            "secondary_outcome": "last model candidate passes independent six tests, regardless of workflow gate",
            "knowledge_policy": "isolated synthetic analogue corpus; no online publication or cross-task learning",
            "prompt_policy": "same production template code; retrieved content and conditional case-assessment instructions differ",
            "generation_parameters": "provider defaults; model alias only; no deterministic seed guarantee",
            "cost_limitation": "No configured prices: budget value recorded, billable cost unknown; time/tokens bounded",
            "unknown_usage_policy": "stop current task immediately; reserve its full token allowance; no retry after unknown usage",
            "reserved_token_ceiling": len(schedule) * budget["tokens"] + 10000,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "runs": {arm: {**common, "patch_dir": f"{arm}/patches", "trace_dir": f"{arm}/traces"}
                     for arm in ("baseline", "rag")}}
    write_once(output / "experiment.json", plan)
    write_once(output / "plan-hash.json", {"sha256": sha(output / "experiment.json")})
    return plan


def verify_plan(output):
    plan = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
    if sha(output / "experiment.json") != json.loads((output / "plan-hash.json").read_text())["sha256"]:
        raise ValueError("Experiment plan changed")
    if plan["runtime"] != runtime_identity() or plan["runner_sha256"] != sha(Path(__file__)):
        raise ValueError("Runtime or runner changed after registration")
    benchmark_bytes = (output / "benchmark.json").read_text(encoding="utf-8")
    output_benchmark = json.loads(benchmark_bytes)
    output_digest = (bench.digest(json.dumps(output_benchmark, ensure_ascii=False, sort_keys=True))
                     if "full_dataset_sha256" in plan else bench.digest(benchmark_bytes))
    if output_digest != plan["dataset_sha256"]:
        raise ValueError("Selected dataset changed")
    full_digest = bench.digest((bench.ROOT / "benchmark.json").read_text(encoding="utf-8"))
    if plan.get("full_dataset_sha256", full_digest) != full_digest:
        raise ValueError("Full dataset changed")
    if "case_ids" in plan:
        full = json.loads((bench.ROOT / "benchmark.json").read_text(encoding="utf-8"))
        if output_benchmark != select_benchmark(full, plan["case_ids"]):
            raise ValueError("Selected cases do not match registered full dataset")
        expected_ids = [record["case_id"] for record in output_benchmark["cases"]]
        if [item["case_id"] for item in plan["inputs"]] != expected_ids or sorted(
                tuple(slot) for slot in plan["schedule"]) != sorted(
                (case_id, arm) for case_id in expected_ids for arm in ("baseline", "rag")):
            raise ValueError("Agent inputs or paired schedule differ from registered subset")
    for item in plan["inputs"]:
        directory = output / "inputs" / item["case_id"]
        if {p.name: sha(p) for p in directory.iterdir()} != item["files"]:
            raise ValueError("Agent input changed or unexpected file entered input directory")
    for arm, key in (("rag", "corpus_sha256"), ("baseline", "empty_corpus_sha256")):
        if sha(output / f"{arm}.sqlite3") != plan[key]:
            raise ValueError("Corpus changed")
    return plan


def select_benchmark(full, case_ids=None):
    available = {record["case_id"] for record in full["cases"]}
    selected = ([record["case_id"] for record in full["cases"]]
                if case_ids is None else list(case_ids))
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= available:
        raise ValueError("case_ids 必须是 benchmark 中不重复的案例编号")
    return {**full, "cases": [record for record in full["cases"] if record["case_id"] in selected]}


def worker(output, case_id, arm):
    plan = verify_plan(output)
    configure(output, plan)
    budget = plan["budget"]
    directory = output / arm / case_id
    if (directory / "result.json").exists():
        raise ValueError("Run slot already completed; no selective rerun")
    directory.mkdir(parents=True, exist_ok=True)
    input_dir = output / "inputs" / case_id
    task_input = json.loads((input_dir / "task.json").read_text(encoding="utf-8"))
    task_id = digest([plan["experiment_id"], arm, case_id])[:32]
    report = task_input["report"] + "\n明确行为契约：" + task_input["contract"]
    state = create_initial_state(task_id, {"type": "python_file", "path": str(input_dir / "target.py")}, report,
        remaining_seconds=budget["runtime_seconds"], remaining_tokens=budget["tokens"],
        remaining_attempts=budget["repair_attempts"], remaining_cost=budget["cost_yuan"])
    last_patch, snapshots, workspace, checkpoint = "", 0, None, None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rag-ab-task-") as temp:
        source = Path(temp) / "source"
        source.mkdir()
        for name in ("target.py", "test_existing.py"):
            shutil.copyfile(input_dir / name, source / name)
        workspace = Path(temp) / "task" / "workspace"
        state["source"]["path"] = str(source / "target.py")
        with execution_scope(budget["runtime_seconds"], task_id=task_id, trace_path=directory / "trace.jsonl",
                             circuit_path=directory / "circuits.sqlite3", max_tokens=budget["tokens"],
                             max_cost=budget["cost_yuan"], on_request=stop_unknown_usage) as context:
            try:
                checkpoint = create_checkpoint_workspace(source, workspace)
                live = KnowledgeBase(output / f"{arm}.sqlite3", qdrant_index=None)
                task = {"state": state, "config": {"allowed_paths": ["target.py"], "test_targets": ["test_existing.py"],
                    "execution_backend": "docker", "deployment_mode": "untrusted", "source_fingerprint": {},
                    "checkpoint": checkpoint, "workspace": str(workspace), "source_root": str(source)}}
                manifest, _ = freeze(task, directory, {"report": report}, live)
                if manifest["models"]["model"] != plan["model"] or manifest["program"]["runtime"] != plan["runtime"]:
                    raise ValueError("Frozen runtime/model differs from registered plan")
                write_once(directory / "task-config.json", task["config"])
                with task_configuration(task, directory):
                    toolbox = RepairToolbox(workspace_root=workspace, source_root=source,
                        checkpoint_revision=checkpoint, allowed_paths=("target.py",), test_targets=("test_existing.py",),
                        execution_backend="docker", knowledge_base=SnapshotKnowledge(live, directory, manifest),
                        task_id=task_id, validation_report_path=directory / "validation.md")
                    def baseline(value):
                        record = reproduce(source={"type": "git_repository", "path": str(workspace),
                            "revision": checkpoint, "analysis_paths": ["target.py"]}, error_report=report,
                            work_root=directory / "reproduction", timeout=min(20, context.remaining()),
                            execution_backend="docker", task_id=task_id)
                        matched = record.get("status") == "已复现，与报告一致"
                        result = {"matched": matched, "record": record}
                        if not matched:
                            result["question"] = record.get("question") or "无法确认复现"
                        return result
                    graph = build_repair_workflow(toolbox.as_repair_services(baseline))
                    for update in graph.stream(state, config={"recursion_limit": 100}, stream_mode="values"):
                        state = dict(update)
                        snapshots += 1
                        save(directory / "states" / f"{snapshots:03}.json", state)
                        if state.get("patch_diff"):
                            last_patch = state["patch_diff"]
                            (directory / f"candidate-{snapshots:03}.patch").write_text(last_patch, encoding="utf-8")
            except Exception as error:
                state.update(status="failed", error_info=error_info(error, operation="ab.worker"))
            finally:
                trace_workflow_end(state)
                result = {"arm": arm, "case_id": case_id, "task_id": task_id,
                          "state": state, "model_tokens": context.tokens, "usage_complete": context.usage_known,
                          "cost_known": context.cost_known, "estimated_cost": context.cost if context.cost_known else None,
                          "elapsed_seconds": time.monotonic() - started,
                          "candidate_sha256": bench.digest(last_patch) if last_patch else None}
                try:
                    with cleanup_scope():
                        result["container_cleanup"] = cleanup_docker_task(task_id)
                        if checkpoint:
                            remove_checkpoint_workspace(workspace, checkpoint)
                    result["workspace_cleanup"] = True
                except Exception as error:
                    result.update(workspace_cleanup=False, cleanup_error=error_info(error, operation="ab.cleanup"))
                result["elapsed_seconds"] = time.monotonic() - started
                export_artifacts(output, arm, case_id, directory, last_patch)
                save(directory / "result.json", result)
    print(json.dumps({k: result[k] for k in ("arm", "case_id", "model_tokens", "elapsed_seconds")}
                     | {"status": state["status"], "candidate": bool(last_patch)}, ensure_ascii=False), flush=True)


def launch(output, case_id, arm):
    directory = output / arm / case_id
    if (directory / "result.json").exists():
        if not all((output / arm / kind / f"{case_id}.{ext}").is_file()
                   for kind, ext in (("patches", "patch"), ("traces", "jsonl"))):
            raise ValueError("Completed slot has missing artifacts; refusing silent rerun")
        return  # Resume dispatch only; completed failed slots are never rerun.
    directory.mkdir(parents=True, exist_ok=True)
    result = None
    started = time.monotonic()
    try:
        budget = json.loads((output / "experiment.json").read_text(encoding="utf-8"))["budget"]
        result = run_process([sys.executable, "-X", "utf8", "-m", "evaluation.run_agent_ab", "worker",
                              "--output", str(output), "--case-id", case_id, "--arm", arm],
                             cwd=ROOT, capture_output=True, timeout=budget["runtime_seconds"] + 30)
        (directory / "worker.stdout").write_bytes(result.stdout)
        (directory / "worker.stderr").write_bytes(result.stderr)
    except Exception as error:
        save(directory / "supervisor-error.json", error_info(error, operation="ab.supervisor"))
    if not (directory / "result.json").exists():
        task_id = digest([output.name, arm, case_id])[:32]
        cleaned = cleanup_docker_task(task_id)
        candidates = sorted(directory.glob("candidate-*.patch"))
        patch = candidates[-1].read_text(encoding="utf-8") if candidates else ""
        path = directory / "trace.jsonl"
        trace("ab.supervisor", "failed", task_id=task_id, path=path, details={"worker_exit": result.returncode if result else None})
        export_artifacts(output, arm, case_id, directory, patch)
        save(directory / "result.json", {"arm": arm, "case_id": case_id, "task_id": task_id,
             "state": {"status": "failed", "last_error": "worker_did_not_finish"},
             "model_tokens": None, "cost_known": False, "estimated_cost": None,
             "container_cleanup": cleaned, "workspace_cleanup": False,
             "elapsed_seconds": time.monotonic() - started})
    saved = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    print(f"{arm} {case_id}: {saved['state']['status']}; tokens={saved['model_tokens']}; seconds={saved['elapsed_seconds']:.1f}", flush=True)


def summarize(pairs):
    both = sum(a and b for a, b in pairs)
    gained = sum(not a and b for a, b in pairs)
    lost = sum(a and not b for a, b in pairs)
    n = gained + lost
    exact_p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / 2**n) if n else 1.0
    return {"both_success": both, "rag_only_success": gained, "baseline_only_success": lost,
            "both_failed": len(pairs) - both - gained - lost,
            "uplift_count": gained - lost, "uplift_percentage_points": (gained - lost) * 100 / len(pairs),
            "mcnemar_exact_two_sided_p": exact_p}


def primary_success(result, patch, candidate_ok):
    return bool(candidate_ok and result["state"]["status"] == "reviewable"
                and result.get("container_cleanup") and result.get("workspace_cleanup")
                and result["state"].get("patch_hash") == bench.digest(patch))


def inspect_trace(path):
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events or any(not isinstance(e, dict) or not e.get("operation") for e in events):
        raise ValueError("Missing or invalid Agent Trace")
    return {"events": len(events),
            "model_requests": sum(e["operation"] == "model.request" for e in events),
            "model_responses": sum(e["operation"] == "model.response" for e in events),
            "errors": sorted({e["error_code"] for e in events if e.get("error_code")})}


def score_candidate(record, patch, root, evidence):
    bench.copy_task(record, root)
    application = bench.apply_candidate(root, patch)
    tests = {"infrastructure_ok": False, "groups": {}}
    if not application["applied"]:
        return application, tests
    command = ["python", "-B", "-m", "pytest", "-q", "-c", "/dev/null", "-p", "no:cacheprovider",
               "--junitxml=/workspace/result.xml", "test_existing.py", "test_acceptance.py"]
    task_id = "score-" + root.name
    try:
        observed = run_execution(command, workspace=root, timeout=30, backend="docker", task_id=task_id,
            extra_mounts=tuple(BindMount(root / name, "/workspace/" + name, True)
                               for name in ("test_existing.py", "test_acceptance.py")))
        groups = {g: dict(total=0, passed=0, failed=0, errors=0, skipped=0) for g in ("test_existing", "test_acceptance")}
        if (root / "result.xml").exists():
            evidence.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / "result.xml", evidence / "result.xml")
            for test in ET.parse(root / "result.xml").iter("testcase"):
                group = test.get("classname", "").split(".")[-1]
                if group not in groups:
                    continue
                groups[group]["total"] += 1
                status = next((v for k, v in (("error", "errors"), ("failure", "failed"), ("skipped", "skipped"))
                               if test.find(k) is not None), "passed")
                groups[group][status] += 1
        tests = {"infrastructure_ok": observed.returncode in (0, 1) and all(
            g["total"] == 3 and g["errors"] == g["skipped"] == 0 for g in groups.values()),
            "groups": groups, "return_code": observed.returncode, "stdout": observed.stdout, "stderr": observed.stderr}
    except Exception as error:
        tests["error"] = error_info(error, operation="ab.score")
    finally:
        tests["container_cleanup"] = cleanup_docker_task(task_id)
        tests["infrastructure_ok"] = bool(tests["infrastructure_ok"] and tests["container_cleanup"])
    save(evidence / "tests.json", tests)
    return application, tests


def token_usage_complete(result):
    """只有拿到用量且任务确认没有未知请求时，才能称用量完整。"""
    accounting = result.get("state", {}).get("accounting") or {}
    usage_complete = result.get("usage_complete", accounting.get("usage_complete"))
    return result.get("model_tokens") is not None and usage_complete is True


def score(output):
    plan = verify_plan(output)
    configure(output, plan)
    records = json.loads((output / "benchmark.json").read_text(encoding="utf-8"))["cases"]
    report = {"experiment_id": plan["experiment_id"], "model": plan["model"], "budget": plan["budget"],
              "measured": True, "scope": f"real Agent + real Docker; {len(records)} authored synthetic tasks; one paired repetition",
              "cost_known": False, "arms": {}}
    with tempfile.TemporaryDirectory(prefix="rag-ab-score-") as temp:
        for arm in ("baseline", "rag"):
            cases = []
            for record in records:
                case_id = record["case_id"]
                result = json.loads((output / arm / case_id / "result.json").read_text(encoding="utf-8"))
                patch = (output / arm / "patches" / f"{case_id}.patch").read_text(encoding="utf-8")
                root = Path(temp) / f"{arm}-{case_id}"
                application, tests = score_candidate(record, patch, root, output / arm / case_id / "independent-tests")
                candidate_ok = all(bench.passed_group(tests, name, 3) for name in ("test_existing", "test_acceptance"))
                state = result["state"]
                primary = primary_success(result, patch, candidate_ok)
                trace_info = inspect_trace(output / arm / "traces" / f"{case_id}.jsonl")
                case = {"case_id": case_id, "workflow_status": state["status"], "repair_success": primary,
                        "candidate_success": candidate_ok, "application": application, "tests": tests,
                        "tokens": result["model_tokens"],
                        "tokens_complete": token_usage_complete(result),
                        "seconds": result["elapsed_seconds"],
                        "trace": trace_info, "case_matches": state.get("case_matches", []),
                        "reproduction_status": (state.get("reproduction") or {}).get("status"),
                        "error": (state.get("error_info") or {}).get("code"), "last_error": state.get("last_error")}
                cases.append(case)
            expected = sum(r["expected_existing_tests"] for r in records)
            passed = sum(x["tests"]["groups"].get("test_existing", {}).get("passed", 0) for x in cases)
            times = sorted(x["seconds"] for x in cases)
            report["arms"][arm] = {"tasks": len(records), "repair_successes": sum(x["repair_success"] for x in cases),
                "candidate_successes": sum(x["candidate_success"] for x in cases),
                "existing_tests_passed": passed, "existing_tests_expected": expected, "regression_pass_rate": passed / expected,
                "candidate_test_execution_tasks": sum(x["tests"]["infrastructure_ok"] for x in cases),
                "tokens": sum(x["tokens"] or 0 for x in cases), "tokens_complete": all(x["tokens_complete"] for x in cases),
                "mean_seconds": sum(times) / len(times), "p95_seconds": times[math.ceil(0.95 * len(times)) - 1],
                "model_requests": sum(x["trace"]["model_requests"] for x in cases),
                "model_responses": sum(x["trace"]["model_responses"] for x in cases), "cases": cases}
    report["paired"] = summarize(list(zip(
        [x["repair_success"] for x in report["arms"]["baseline"]["cases"]],
        [x["repair_success"] for x in report["arms"]["rag"]["cases"]])))
    report["limitations"] = ["Synthetic small tasks and deliberately related history; not production defects",
        "One repetition; stochastic model/provider defaults; paired difference is descriptive",
        "Prices unavailable: monetary benefit and hard bill ceiling not established",
        "Missing candidates count as failures and no executed existing tests; coverage reported separately",
        "Primary requires production workflow gate; secondary scores actual model candidate independently"]
    save(output / "report.json", report)
    print(json.dumps({"arms": {k: {f:v for f,v in a.items() if f != "cases"} for k,a in report["arms"].items()},
                      "paired": report["paired"]}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "worker", "score"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--case-ids", help="prepare 时选择逗号分隔的案例编号")
    parser.add_argument("--task-tokens", type=int, help="prepare 时冻结每个任务的 Token 上限，两组相同")
    parser.add_argument("--arm", choices=("baseline", "rag"))
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        prepare(output, [item.strip() for item in args.case_ids.split(",") if item.strip()] if args.case_ids else None,
                task_tokens=args.task_tokens)
    elif args.mode == "worker":
        worker(output, args.case_id, args.arm)
    elif args.mode == "run":
        plan = verify_plan(output)
        with ThreadPoolExecutor(max_workers=plan["concurrency"]) as pool:
            list(pool.map(lambda slot: launch(output, *slot), plan["schedule"]))
    else:
        score(output)


if __name__ == "__main__":
    main()
