"""Validate fixtures and score actual patches; no model calls by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_base import (
    WRRF_RANK_CONSTANT,
    KnowledgeBase,
    _case_search_text,
    _weighted_reciprocal_rank_scores,
    build_case_document,
)
from repair_state import create_initial_state

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
        write_text(destination / filename, (source / filename).read_text(encoding="utf-8"))
    write_text(destination / "test_acceptance.py",
               (dataset_path(record["oracle"]) / "test_acceptance.py").read_text(encoding="utf-8"))
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


def ranking_metrics(ranked_ids: list[str], relevant_ids: list[str], ks=(1, 3, 5, 10)) -> dict[str, float]:
    """计算单个查询的 Recall@K、Precision@K 和 MRR@K。"""
    relevant = set(relevant_ids)
    metrics = {}
    first_rank = next((index for index, case_id in enumerate(ranked_ids, 1) if case_id in relevant), None)
    metrics["mrr"] = 1 / first_rank if first_rank is not None else 0.0
    for k in ks:
        hits = sum(case_id in relevant for case_id in ranked_ids[:k])
        metrics[f"recall@{k}"] = hits / len(relevant) if relevant else 0.0
        metrics[f"precision@{k}"] = hits / k
        metrics[f"mrr@{k}"] = 1 / first_rank if first_rank is not None and first_rank <= k else 0.0
    return metrics


def aggregate_ranking_metrics(per_query: list[dict[str, float]]) -> dict[str, float]:
    """对各查询的指标取宏平均。"""
    if not per_query:
        return {}
    keys = per_query[0]
    return {key: sum(item[key] for item in per_query) / len(per_query) for key in keys}


def legacy_hybrid_score(match: dict[str, object]) -> float:
    """Reproduce the previous weighted-score fusion for same-candidate comparisons."""
    keyword = float(match.get("keyword_score") or 0.0)
    vector = match.get("vector_score")
    if vector is None:
        return keyword
    return 0.7 * max(0.0, float(vector)) + 0.3 * keyword


def evaluate_outcome_and_scope_filters(directory: Path) -> dict:
    """检查成功/失败经验分开召回，以及指定范围严格排除缺元数据案例。"""
    knowledge = KnowledgeBase(
        directory / "filter-contract.sqlite3",
        embedder=lambda _text: [1.0, 0.0],
        qdrant_index=None,
    )
    scope = {"repository_id": "repo-a", "file_paths": ["src/worker.py"],
             "dependency_versions": {"library": "2.1"}}
    accepted = create_initial_state(
        "accepted-match", {"type": "python_file", "path": "src/worker.py"}, "timeout in worker",
        remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
    )
    accepted.update(
        review_decision="accepted", diagnosis="The worker retries without a bounded delay.",
        patch_diff="accepted patch", patch_hash="accepted-hash", source={"retrieval_scope": scope},
        applicable_conditions=["worker timeout"],
        validation={"passed": True, "patch_hash": "accepted-hash", "summary": "tests passed"},
    )
    knowledge.save_case(accepted)
    old = {**accepted, "task_id": "accepted-old", "source": {}, "review_decision": "accepted"}
    knowledge.save_case(old)
    rejected = {**accepted, "task_id": "rejected-match", "review_decision": "rejected",
                "review_reason": "The patch retries forever under this dependency version."}
    knowledge.record_rejection(rejected)
    filters = {"repository_id": "repo-a", "file_paths": ["src/worker.py"],
               "dependency_versions": {"library": "2.1"}}
    accepted_ids = [item["case_id"] for item in knowledge.search("worker timeout", outcome="accepted", filters=filters)]
    rejected_ids = [item["case_id"] for item in knowledge.search("worker timeout", outcome="rejected", filters=filters)]
    passed = accepted_ids == ["accepted-match"] and rejected_ids == ["rejected-match"]
    return {"passed": passed, "accepted_ids": accepted_ids, "rejected_ids": rejected_ids,
            "excluded_missing_metadata": "accepted-old" not in accepted_ids}


def evaluate_retrieval(records: list[dict], histories: list[dict], labels: dict,
                       directory: Path, mode: str, min_similarity: float) -> dict:
    if mode == "configured-embeddings":
        from llm_client import embed_texts
    else:
        embed_texts = None
    from retrieval_reranker import CrossEncoderReranker
    reranker = CrossEncoderReranker.from_environment() if mode == "configured-embeddings" else None
    strategies = ["keyword"] if mode == "keyword" else ["keyword", "vector", "hybrid"]
    if reranker is not None:
        strategies.append("hybrid+cross_encoder")
    case_states = []
    for document in histories:
        patch = document["patch_diff"]
        case_states.append({
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
    case_texts = [_case_search_text(json.loads(build_case_document(state))) for state in case_states]
    embedding_inputs = case_texts + [record["query"] for record in records]
    if embed_texts is None:
        embedding_vectors = [[0.0] for _ in embedding_inputs]
    else:
        embedding_vectors = embed_texts(embedding_inputs, batch_size=8)
    embedding_cache = dict(zip(embedding_inputs, embedding_vectors))

    def cached_embed(text):
        """从预先分批生成的向量中读取结果，不在查询循环中重复调用服务。"""
        if text not in embedding_cache:
            raise RuntimeError("评测输入没有预生成 embedding")
        return embedding_cache[text]

    # Keep synthetic retrieval runs isolated from the shared Qdrant collection.
    knowledge = KnowledgeBase(
        directory / "retrieval.sqlite3",
        embedder=cached_embed,
        reranker=reranker,
        qdrant_index=None,
    )
    for state in case_states:
        knowledge.save_case(state)
    evaluations = {}
    legacy_per_query = []
    legacy_details = []
    wrrf_weight_per_query = {weight: [] for weight in range(1, 10)}
    wrrf_weight_details = {weight: [] for weight in range(1, 10)}
    for strategy in strategies:
        strategy_name = "hybrid" if strategy == "hybrid+cross_encoder" else strategy
        per_query = []
        details = []
        for record in records:
            matches = knowledge.search(
                record["query"], limit=len(histories), min_similarity=min_similarity,
                strategy=strategy_name, rerank=(strategy == "hybrid+cross_encoder"),
            )
            identifiers = [match["case_id"] for match in matches]
            relevant = labels[record["case_id"]]
            metrics = ranking_metrics(identifiers[:10], relevant)
            per_query.append(metrics)
            if strategy == "hybrid":
                vector_ranking = sorted(
                    (item for item in matches
                     if item["vector_score"] is not None
                     and float(item["vector_score"]) >= min_similarity
                     and float(item["vector_score"]) > 0),
                    key=lambda item: (-float(item["vector_score"]), str(item["case_id"])),
                )
                keyword_ranking = sorted(
                    (item for item in matches if float(item["keyword_score"]) > 0),
                    key=lambda item: (-float(item["keyword_score"]), str(item["case_id"])),
                )
                vector_ids = [str(item["case_id"]) for item in vector_ranking]
                keyword_ids = [str(item["case_id"]) for item in keyword_ranking]
                for vector_tenths in range(1, 10):
                    vector_weight = vector_tenths / 10
                    keyword_weight = 1 - vector_weight
                    fused = _weighted_reciprocal_rank_scores(
                        vector_ids,
                        keyword_ids,
                        vector_weight=vector_weight,
                        keyword_weight=keyword_weight,
                    )
                    fused_ids = sorted(fused, key=lambda case_id: (-fused[case_id], case_id))
                    fused_metrics = ranking_metrics(fused_ids[:10], relevant)
                    wrrf_weight_per_query[vector_tenths].append(fused_metrics)
                    wrrf_weight_details[vector_tenths].append({
                        "case_id": record["case_id"],
                        "ranked": fused_ids,
                        "relevant": relevant,
                        "metrics": fused_metrics,
                    })
                legacy_matches = sorted(
                    matches,
                    key=lambda item: (-legacy_hybrid_score(item), str(item["case_id"])),
                )
                legacy_ranked_ids = [str(item["case_id"]) for item in legacy_matches]
                legacy_metrics = ranking_metrics(legacy_ranked_ids[:10], relevant)
                legacy_per_query.append(legacy_metrics)
                legacy_details.append({
                    "case_id": record["case_id"],
                    "ranked": legacy_ranked_ids,
                    "relevant": relevant,
                    "metrics": legacy_metrics,
                })
            details.append({"case_id": record["case_id"], "query": record["query"],
                            "ranked": identifiers, "relevant": relevant, "metrics": metrics,
                            "returned_modes": [match["retrieval_mode"] for match in matches]})
        aggregate = aggregate_ranking_metrics(per_query)
        evaluations[strategy] = {"metrics": aggregate, "details": details}
    if legacy_per_query:
        evaluations["hybrid_legacy_score"] = {
            "metrics": aggregate_ranking_metrics(legacy_per_query),
            "details": legacy_details,
            "comparison_scope": "same full eligible result pool as WRRF; both rankings scored at top 10",
        }
        evaluations["hybrid_wrrf_weight_sweep"] = {
            "rank_constant": WRRF_RANK_CONSTANT,
            "weights": {
                f"{vector_tenths / 10:.1f}/{1 - vector_tenths / 10:.1f}": {
                    "metrics": aggregate_ranking_metrics(wrrf_weight_per_query[vector_tenths]),
                    "details": wrrf_weight_details[vector_tenths],
                }
                for vector_tenths in range(1, 10)
            },
            "comparison_scope": "same full eligible result pool, query embeddings, and vector/keyword ranks; top-10 metrics",
        }
    primary = "hybrid" if mode == "configured-embeddings" else "keyword"
    primary_details = evaluations[primary]["details"]
    hits = sum(bool(set(item["ranked"][:5]).intersection(item["relevant"])) for item in primary_details)
    return {
        "measured": True, "method": mode, "min_similarity": min_similarity,
        "corpus_size": len(histories), "queries": len(records), "hits": hits,
        "top5_hit_rate": hits / len(records), "metrics_by_strategy": evaluations,
        "details": [{"case_id": item["case_id"], "query": item["query"],
                     "top5": item["ranked"][:5], "relevant": item["relevant"],
                     "hit": bool(set(item["ranked"][:5]).intersection(item["relevant"])),
                     "returned_modes": item["returned_modes"]} for item in primary_details],
        "not_measured": ({
            "hybrid+cross_encoder": "install the rerank extra and configure CROSS_ENCODER_MODEL"
        } if mode == "configured-embeddings" and reranker is None else {} if mode == "configured-embeddings" else {
            "vector": "configured embeddings service is required",
            "hybrid": "configured embeddings service is required",
            "hybrid+cross_encoder": "configured embeddings and CROSS_ENCODER_MODEL are required",
        }),
        "limitation": "Synthetic authored labels; configured service modes require real credentials and model runs",
    }


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


def load_experiment_manifest(path: Path, benchmark_hash: str) -> dict:
    """读取并校验配对 Agent 实验配置、预算以及产物目录。"""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("实验清单根节点必须是 JSON 对象")
    if not isinstance(manifest.get("experiment_id"), str) or not manifest["experiment_id"].strip():
        raise ValueError("实验清单必须提供 experiment_id")
    if not isinstance(manifest.get("model"), str) or not manifest["model"].strip():
        raise ValueError("实验清单必须固定 model")
    if not isinstance(manifest.get("prompt_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["prompt_sha256"]):
        raise ValueError("prompt_sha256 必须是 64 位小写 SHA-256")
    if manifest.get("dataset_sha256") != benchmark_hash:
        raise ValueError("实验数据集哈希与当前 benchmark.json 不一致")
    budget = manifest.get("budget")
    required_budget = {"runtime_seconds", "tokens", "cost_yuan", "repair_attempts"}
    if not isinstance(budget, dict) or set(budget) != required_budget:
        raise ValueError("实验清单必须完整记录运行时间、Token、费用和修复次数预算")
    for field in ("runtime_seconds", "tokens", "repair_attempts"):
        if isinstance(budget[field], bool) or not isinstance(budget[field], int) or budget[field] <= 0:
            raise ValueError(f"budget.{field} 必须是正整数")
    if isinstance(budget["cost_yuan"], bool) or not isinstance(budget["cost_yuan"], (int, float)) or budget["cost_yuan"] <= 0:
        raise ValueError("budget.cost_yuan 必须是正数")
    runs = manifest.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"baseline", "rag"}:
        raise ValueError("实验清单必须分别提供 baseline 和 rag 运行")
    resolved_runs = {}
    for name in ("baseline", "rag"):
        run = runs[name]
        if not isinstance(run, dict) or run.get("model") != manifest["model"]:
            raise ValueError(f"{name} 必须使用实验清单中固定的模型")
        if run.get("prompt_sha256") != manifest["prompt_sha256"]:
            raise ValueError(f"{name} 必须使用相同提示词哈希")
        if run.get("budget") != budget:
            raise ValueError(f"{name} 必须使用相同预算")
        resolved_runs[name] = {}
        for field in ("patch_dir", "trace_dir"):
            raw = run.get(field)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"runs.{name}.{field} 必须是相对或绝对目录")
            directory = Path(raw)
            resolved_runs[name][field] = (path.parent / directory).resolve() if not directory.is_absolute() else directory
    return {**manifest, "runs": resolved_runs}


def validate_experiment_artifacts(records: list[dict], run: dict) -> dict:
    """确认每个实际补丁有对应 Trace，并为产物生成可复核摘要。"""
    issues = []
    artifacts = []
    for record in records:
        case_id = record["case_id"]
        patch_path = run["patch_dir"] / f"{case_id}.patch"
        trace_path = run["trace_dir"] / f"{case_id}.jsonl"
        if not patch_path.is_file():
            issues.append(f"{case_id}: missing patch")
            continue
        if not trace_path.is_file():
            issues.append(f"{case_id}: missing trace")
            continue
        try:
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            issues.append(f"{case_id}: invalid trace ({type(error).__name__})")
            continue
        if not events:
            issues.append(f"{case_id}: empty trace")
            continue
        artifacts.append({"case_id": case_id,
                          "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
                          "trace_path": str(trace_path), "trace_events": len(events)})
    return {"complete": not issues, "issues": issues, "artifacts": artifacts}


def evaluate_agent(
    records: list[dict], baseline: Path | None, rag: Path | None, directory: Path,
    experiment: dict | None = None,
) -> dict:
    """计分配对补丁；只有固定配置和完整 Trace 证据才标记真实 Agent 实验。"""
    if baseline is None or rag is None:
        return {"measured": False, "reason": "No paired Agent patch submissions; reference patches do not measure repair ability"}
    before = evaluate_patches(records, baseline, directory / "baseline")
    after = evaluate_patches(records, rag, directory / "rag")
    comparison = {"baseline": before, "rag": after,
                  "uplift": after["repair_successes"] - before["repair_successes"]}
    if experiment is None:
        return {"measured": False, "patch_comparison": comparison,
                "reason": "Patch behavior was scored, but fixed model/prompt/budget and per-case Trace artifacts were not supplied"}
    baseline_artifacts = validate_experiment_artifacts(records, experiment["runs"]["baseline"])
    rag_artifacts = validate_experiment_artifacts(records, experiment["runs"]["rag"])
    measured = baseline_artifacts["complete"] and rag_artifacts["complete"]
    return {
        "measured": measured,
        "experiment": {key: experiment[key] for key in ("experiment_id", "model", "prompt_sha256", "dataset_sha256", "budget")},
        "patch_comparison": comparison,
        "baseline_artifacts": baseline_artifacts,
        "rag_artifacts": rag_artifacts,
        "reason": None if measured else "At least one run lacks a valid patch or Trace for every benchmark case",
        "limitation": "Synthetic benchmark and authored relevance labels do not establish production performance",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the synthetic 30-case evaluation dataset")
    parser.add_argument("--retrieval-mode", choices=("keyword", "configured-embeddings"), default="keyword",
                        help="configured-embeddings explicitly calls the configured external embedding service")
    parser.add_argument("--min-similarity", type=float, default=0.35)
    parser.add_argument("--baseline-patches", type=Path)
    parser.add_argument("--rag-patches", type=Path)
    parser.add_argument("--experiment-manifest", type=Path,
                        help="配对 Agent 实验清单；固定模型、提示词哈希、数据集哈希、预算和两组产物目录")
    parser.add_argument("--output", type=Path, default=ROOT / "results.json")
    args = parser.parse_args()
    if (args.baseline_patches is None) != (args.rag_patches is None):
        parser.error("必须同时提供 --baseline-patches 和 --rag-patches")
    if args.experiment_manifest is not None and (args.baseline_patches is not None or args.rag_patches is not None):
        parser.error("使用 --experiment-manifest 时，请在清单中填写两组补丁目录，不要重复传入补丁参数")
    if not 0 <= args.min_similarity <= 1:
        parser.error("--min-similarity 必须在 0 到 1 之间")
    payload = json.loads((ROOT / "benchmark.json").read_text(encoding="utf-8"))
    benchmark_hash = digest((ROOT / "benchmark.json").read_text(encoding="utf-8"))
    try:
        experiment = load_experiment_manifest(args.experiment_manifest, benchmark_hash) if args.experiment_manifest else None
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(f"实验清单无效：{error}")
    baseline_patches = args.baseline_patches
    rag_patches = args.rag_patches
    if experiment is not None:
        baseline_patches = experiment["runs"]["baseline"]["patch_dir"]
        rag_patches = experiment["runs"]["rag"]["patch_dir"]
    for directory in (baseline_patches, rag_patches):
        if directory is not None and not directory.is_dir():
            parser.error(f"补丁目录不存在：{directory}")
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
        filter_evaluation = evaluate_outcome_and_scope_filters(directory) if valid else {"measured": False, "reason": "Invalid dataset"}
        agent = evaluate_agent(records, baseline_patches, rag_patches, directory, experiment) if valid else {"measured": False, "reason": "Invalid dataset"}
    report = {"benchmark": payload["version"], "targets": TARGETS,
              "dataset_manifest_sha256": digest((ROOT / "benchmark.json").read_text(encoding="utf-8")),
              "status": "valid" if valid else "invalid",
              "self_check": {"valid_tasks": sum(item["valid"] for item in cases),
                             "valid_experiences": sum(item["valid"] for item in experiences),
                             "tasks": cases, "experiences": experiences},
              "retrieval": retrieval, "retrieval_filtering": filter_evaluation, "agent_outcomes": agent,
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
