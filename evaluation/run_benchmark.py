"""Validate fixtures and score actual patches; no model calls by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_base import KnowledgeBase

ROOT = Path(__file__).resolve().parent
TARGETS = {"top5_hit_rate": 0.82, "baseline_successes": 16,
           "rag_successes": 22, "regression_pass_rate": 0.96}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dataset_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("评测文件必须位于 evaluation 目录内")
    return path


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def run_pytest(directory: Path, files: list[str]) -> dict:
    """Count observed JUnit outcomes; collection errors are never defect evidence."""
    write_text(directory / "pytest.ini", "[pytest]\n")
    report = directory / "junit.xml"
    report.unlink(missing_ok=True)
    command = [sys.executable, "-B", "-m", "pytest", "-q", "-c", "pytest.ini",
               "-p", "no:cacheprovider", "--junitxml=junit.xml", *files]
    environment = dict(os.environ)
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"):
        environment.pop(name, None)
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONHASHSEED="0")
    try:
        process = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=20)
    except subprocess.TimeoutExpired:
        return {"infrastructure_ok": False, "reason": "pytest_timeout", "groups": {}}
    if not report.exists():
        return {"infrastructure_ok": False, "reason": "missing_junit_report", "groups": {}}
    try:
        tree = ET.parse(report)
    except ET.ParseError:
        return {"infrastructure_ok": False, "reason": "invalid_junit_report", "groups": {}}
    groups = {Path(name).stem: dict(total=0, passed=0, failed=0, errors=0, skipped=0) for name in files}
    failures = []
    for test in tree.iter("testcase"):
        group = test.get("classname", "").split(".")[-1]
        if group not in groups:
            return {"infrastructure_ok": False, "reason": "unexpected_test_group", "groups": groups}
        counts = groups[group]
        counts["total"] += 1
        status = "passed"
        for tag, result in (("error", "errors"), ("failure", "failed"), ("skipped", "skipped")):
            node = test.find(tag)
            if node is not None:
                status = result
                failures.append({"test": test.get("name"), "status": status,
                                 "message": node.get("message", "")[:300]})
                break
        counts[status] += 1
    infrastructure_ok = (process.returncode in (0, 1)
                         and all(item["total"] > 0 and item["errors"] == 0 for item in groups.values()))
    return {"infrastructure_ok": infrastructure_ok, "return_code": process.returncode,
            "groups": groups, "failures": failures}


def apply_candidate(directory: Path, patch: str) -> dict:
    """Use git's patch parser and restrict the candidate to the target source."""
    if not isinstance(patch, str) or not patch.strip():
        return {"applied": False, "reason": "empty_patch"}
    data = patch.encode("utf-8")
    result = {"applied": False, "patch_sha256": digest(patch)}
    try:
        stat = subprocess.run(["git", "apply", "--numstat", "-z", "-"], cwd=directory,
                              input=data, capture_output=True, timeout=10)
        rows = stat.stdout.decode("utf-8").rstrip("\0").split("\0")
        if stat.returncode or not rows or any(row.split("\t")[2:] != ["target.py"] for row in rows):
            return {**result, "reason": "invalid_patch_or_protected_path"}
        for arguments in (["--check"], []):
            applied = subprocess.run(["git", "apply", "--whitespace=error", *arguments, "-"],
                                     cwd=directory, input=data, capture_output=True, timeout=10)
            if applied.returncode:
                return {**result, "reason": "patch_does_not_apply"}
    except (subprocess.TimeoutExpired, UnicodeError):
        return {**result, "reason": "patch_parser_failure"}
    if not (directory / "target.py").is_file() or (directory / "target.py").is_symlink():
        return {**result, "reason": "target_must_remain_a_regular_file"}
    return {**result, "applied": True}


def copy_task(record: dict, destination: Path) -> None:
    source = dataset_path(record["repository"])
    destination.mkdir()
    for filename in ("target.py", "test_existing.py"):
        shutil.copyfile(source / filename, destination / filename)
    shutil.copyfile(dataset_path(record["oracle"]) / "test_acceptance.py", destination / "test_acceptance.py")
    for name, key in (("target.py", "buggy_source_sha256"),
                      ("test_existing.py", "existing_tests_sha256"),
                      ("test_acceptance.py", "acceptance_tests_sha256")):
        if digest((destination / name).read_text(encoding="utf-8")) != record[key]:
            raise ValueError(f"{record['case_id']} 的 {name} 校验和不匹配")


def passed_group(result: dict, group: str, expected: int) -> bool:
    counts = result.get("groups", {}).get(group, {})
    return result["infrastructure_ok"] and counts.get("passed") == counts.get("total") == expected


def check_task(record: dict, directory: Path) -> dict:
    copy_task(record, directory)
    source = (directory / "target.py").read_text(encoding="utf-8")
    patch = (dataset_path(record["oracle"]) / "reference.patch").read_text(encoding="utf-8")
    if digest(source) != record["buggy_source_sha256"] or digest(patch) != record["reference_patch_sha256"]:
        raise ValueError(f"{record['case_id']} 的源码或参考补丁校验和不匹配")
    files = ["test_existing.py", "test_acceptance.py"]
    baseline = run_pytest(directory, files)
    counts = baseline.get("groups", {}).get("test_acceptance", {})
    baseline_valid = (passed_group(baseline, "test_existing", record["expected_existing_tests"])
                      and counts.get("total") == record["expected_acceptance_tests"]
                      and counts.get("failed", 0) > 0 and counts.get("skipped") == 0)
    applied = apply_candidate(directory, patch)
    reference = run_pytest(directory, files) if applied["applied"] else {"infrastructure_ok": False, "groups": {}}
    reference_valid = all(passed_group(reference, Path(name).stem, record[key])
                          for name, key in zip(files, ("expected_existing_tests", "expected_acceptance_tests")))
    return {"case_id": record["case_id"], "valid": bool(baseline_valid and reference_valid),
            "baseline_exposes_defect": bool(baseline_valid), "reference_passed": bool(reference_valid),
            "reference_patch": applied, "baseline": baseline, "reference": reference}


def check_experience(document: dict, directory: Path) -> dict:
    directory.mkdir()
    write_text(directory / "target.py", document["before_source"])
    write_text(directory / "test_experience.py", document["verification_tests"])
    baseline = run_pytest(directory, ["test_experience.py"])
    counts = baseline.get("groups", {}).get("test_experience", {})
    baseline_valid = (baseline["infrastructure_ok"] and counts.get("total") == 3
                      and counts.get("failed", 0) > 0 and counts.get("skipped") == 0)
    applied = apply_candidate(directory, document["patch_diff"])
    fixed = run_pytest(directory, ["test_experience.py"]) if applied["applied"] else {"infrastructure_ok": False, "groups": {}}
    return {"experience_id": document["experience_id"],
            "valid": bool(baseline_valid and passed_group(fixed, "test_experience", 3)),
            "baseline": baseline, "reference_patch": applied, "reference": fixed}


def disabled_embedding(_text):
    raise RuntimeError("Offline benchmark deliberately disables query embeddings")


def evaluate_retrieval(records: list[dict], histories: list[dict], labels: dict,
                       directory: Path, mode: str, min_similarity: float) -> dict:
    if mode == "configured-embeddings":
        from llm_client import embed_text
        embedder = embed_text
    else:
        embedder = lambda _text: [0.0]
    knowledge = KnowledgeBase(directory / "retrieval.sqlite3", embedder=embedder)
    for document in histories:
        # Production-schema compatibility in a temporary DB, not human review.
        patch = document["patch_diff"]
        knowledge.save_case({
            "task_id": document["experience_id"], "review_decision": "accepted",
            "original_report": document["original_report"], "diagnosis": document["diagnosis"],
            "patch_diff": patch, "patch_hash": digest(patch),
            "source": {"project": "synthetic-evaluation-only", "provenance": document["provenance"],
                       "review": "synthetic_adapter_not_human_review"},
            "reproduction": {"steps": "Run synthetic experience tests"},
            "applicable_conditions": document["applicable_conditions"],
            "validation": {"passed": True, "patch_hash": digest(patch),
                           "summary": "Observed fixture tests passed in this evaluation run"},
        })
    if mode == "keyword":
        knowledge = KnowledgeBase(directory / "retrieval.sqlite3", embedder=disabled_embedding)
    details = []
    for record in records:
        matches = knowledge.search(record["query"], limit=5, min_similarity=min_similarity)
        identifiers = [match["case_id"] for match in matches]
        details.append({"case_id": record["case_id"], "query": record["query"],
                        "top5": identifiers, "relevant": labels[record["case_id"]],
                        "hit": bool(set(identifiers).intersection(labels[record["case_id"]])),
                        "returned_modes": [match["retrieval_mode"] for match in matches]})
    hits = sum(item["hit"] for item in details)
    return {"measured": True, "method": mode, "min_similarity": min_similarity,
            "corpus_size": len(histories), "queries": len(details), "hits": hits,
            "top5_hit_rate": hits / len(details), "details": details,
            "limitation": "Synthetic authored labels; keyword mode does not measure embedding quality"}


def evaluate_patches(records: list[dict], patch_directory: Path, directory: Path) -> dict:
    """Only executed patch outcomes count; missing patches remain unsuccessful."""
    directory.mkdir()
    outcomes = []
    for record in records:
        patch_path = patch_directory / f"{record['case_id']}.patch"
        if not patch_path.is_file():
            outcomes.append({"case_id": record["case_id"], "repair_success": False,
                             "existing_passed": 0, "existing_executed": 0, "reason": "missing_patch"})
            continue
        case_root = directory / record["case_id"]
        copy_task(record, case_root)
        application = apply_candidate(case_root, patch_path.read_text(encoding="utf-8"))
        result = run_pytest(case_root, ["test_existing.py", "test_acceptance.py"]) if application["applied"] else {"infrastructure_ok": False, "groups": {}}
        existing = result.get("groups", {}).get("test_existing", {})
        existing_passed = existing.get("passed", 0) if result["infrastructure_ok"] else 0
        success = (passed_group(result, "test_existing", record["expected_existing_tests"])
                   and passed_group(result, "test_acceptance", record["expected_acceptance_tests"]))
        outcomes.append({"case_id": record["case_id"], "repair_success": bool(success),
                         "existing_passed": existing_passed, "existing_executed": existing.get("total", 0),
                         "application": application, "tests": result})
    expected = sum(record["expected_existing_tests"] for record in records)
    passed = sum(item["existing_passed"] for item in outcomes)
    return {"total_tasks": len(records), "repair_successes": sum(item["repair_success"] for item in outcomes),
            "submitted_patches": sum("application" in item for item in outcomes),
            "existing_tests_expected": expected, "existing_tests_passed": passed,
            "existing_tests_executed": sum(item["existing_executed"] for item in outcomes),
            "regression_pass_rate": passed / expected, "cases": outcomes}


def evaluate_agent(records: list[dict], baseline: Path | None, rag: Path | None, directory: Path) -> dict:
    if baseline is None or rag is None:
        return {"measured": False, "reason": "No paired Agent patch submissions; reference patches do not measure repair ability"}
    before = evaluate_patches(records, baseline, directory / "baseline")
    after = evaluate_patches(records, rag, directory / "rag")
    return {"measured": True, "baseline": before, "rag": after,
            "uplift": after["repair_successes"] - before["repair_successes"],
            "limitation": "Patch correctness measured; Agent provenance, model and budgets must be recorded separately"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the synthetic 30-case evaluation dataset")
    parser.add_argument("--retrieval-mode", choices=("keyword", "configured-embeddings"), default="keyword",
                        help="configured-embeddings explicitly calls the configured external embedding service")
    parser.add_argument("--min-similarity", type=float, default=0.35)
    parser.add_argument("--baseline-patches", type=Path)
    parser.add_argument("--rag-patches", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "results.json")
    args = parser.parse_args()
    if (args.baseline_patches is None) != (args.rag_patches is None):
        parser.error("必须同时提供 --baseline-patches 和 --rag-patches")
    if not 0 <= args.min_similarity <= 1:
        parser.error("--min-similarity 必须在 0 到 1 之间")
    for directory in (args.baseline_patches, args.rag_patches):
        if directory is not None and not directory.is_dir():
            parser.error(f"补丁目录不存在：{directory}")
    payload = json.loads((ROOT / "benchmark.json").read_text(encoding="utf-8"))
    records = payload["cases"]
    labels = json.loads((ROOT / "relevance.json").read_text(encoding="utf-8"))["labels"]
    histories = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "history_cases").glob("*.json"))]
    with tempfile.TemporaryDirectory(prefix="repair-eval-") as temporary:
        directory = Path(temporary)
        cases = []
        for record in records:
            result = check_task(record, directory / record["case_id"])
            cases.append(result)
            print(f"task {record['case_id']}: {'valid' if result['valid'] else 'INVALID'}", flush=True)
        experiences = []
        for document in histories:
            result = check_experience(document, directory / document["experience_id"])
            experiences.append(result)
            print(f"history {document['experience_id']}: {'valid' if result['valid'] else 'INVALID'}", flush=True)
        valid = len(cases) == len(experiences) == 30 and all(item["valid"] for item in cases + experiences)
        retrieval = evaluate_retrieval(records, histories, labels, directory, args.retrieval_mode, args.min_similarity) if valid else {"measured": False, "reason": "Invalid dataset"}
        agent = evaluate_agent(records, args.baseline_patches, args.rag_patches, directory) if valid else {"measured": False, "reason": "Invalid dataset"}
    report = {"benchmark": payload["version"], "targets": TARGETS,
              "dataset_manifest_sha256": digest((ROOT / "benchmark.json").read_text(encoding="utf-8")),
              "status": "valid" if valid else "invalid",
              "self_check": {"valid_tasks": sum(item["valid"] for item in cases),
                             "valid_experiences": sum(item["valid"] for item in experiences),
                             "tasks": cases, "experiences": experiences},
              "retrieval": retrieval, "agent_outcomes": agent,
              "target_assessment": {"agent_targets_measured": agent["measured"],
                                    "min_top5_hits_out_of_30": math.ceil(30 * TARGETS["top5_hit_rate"]),
                                    "min_regression_passes_out_of_90": math.ceil(90 * TARGETS["regression_pass_rate"]),
                                    "note": "Synthetic self-check cannot establish real-world target attainment"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_text(args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "valid_tasks": report["self_check"]["valid_tasks"],
                      "valid_experiences": report["self_check"]["valid_experiences"],
                      "retrieval_top5": retrieval.get("top5_hit_rate"), "agent_measured": agent["measured"]}, ensure_ascii=False))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
