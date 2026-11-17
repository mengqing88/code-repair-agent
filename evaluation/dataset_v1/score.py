"""Score one candidate patch against dataset v1.

The scorer is deliberately separate from the builder: it reads only
``data/manifest.json`` plus the checked in ``data/agent-input`` and
``data/grader-only`` trees, applies the candidate patch to a private copy of the
case and records the hard-gate evidence described in
``docs/evaluation/最终补丁生成评测集要求.md``.

Usage::

    .venv\\Scripts\\python.exe -m evaluation.dataset_v1.score --case dv1-01 --patch cand.diff \\
        --run-id run-001 --out evaluation/dataset_v1/data/results/run-001/dv1-01.json
    .venv\\Scripts\\python.exe -m evaluation.dataset_v1.score --summarize evaluation/dataset_v1/data/results/run-001

Exit codes: 0 = the patch passes every hard gate, 1 = it does not, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow "python score.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.dataset_v1.build import (
        apply_patch, run_tests, sha256_file, sha256_text, tree_sha256,
    )
else:
    from .build import apply_patch, run_tests, sha256_file, sha256_text, tree_sha256

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.json"
AGENT_INPUT = DATA / "agent-input"
GRADER_ONLY = DATA / "grader-only"

CATEGORIES = (
    "no_candidate",
    "patch_format",
    "patch_apply",
    "wrong_behavior",
    "incomplete_fix",
    "regression",
    "scope_violation",
    "test_tampering",
    "clarification_needed",
    "timeout_or_budget",
    "infrastructure",
    "oracle_invalid",
    "unknown",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def load_manifest(path=None):
    path = Path(path or MANIFEST).resolve()
    if not path.exists():
        raise SystemExit("manifest not found: {0}; run evaluation.dataset_v1.build first".format(path))
    digest = sha256_file(path)
    checksum_path = path.parent / "manifest.sha256"
    if not checksum_path.is_file():
        raise SystemExit("manifest checksum file missing: {0}".format(checksum_path))
    try:
        fields = checksum_path.read_text(encoding="utf-8").split()
        expected = fields[0]
        filename = fields[1] if len(fields) > 1 else path.name
    except (OSError, IndexError) as exc:
        raise SystemExit("manifest checksum file is invalid: {0}".format(checksum_path)) from exc
    if filename != path.name or expected != digest:
        raise SystemExit("manifest SHA-256 mismatch: {0}".format(path))
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("manifest is not valid JSON: {0}".format(path)) from exc
    if not isinstance(manifest, dict):
        raise SystemExit("manifest root must be a JSON object")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or manifest.get("case_count") != len(cases):
        raise SystemExit("manifest case_count does not match its registered cases")
    if any(not isinstance(item, dict) for item in cases):
        raise SystemExit("manifest cases must be JSON objects")
    case_ids = [item.get("case_id") for item in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise SystemExit("manifest contains an invalid case_id")
    if len(case_ids) != len(set(case_ids)):
        raise SystemExit("manifest contains duplicate case_id values")
    return manifest


def _case_roots(manifest_path, agent_root=None, grader_root=None):
    data_root = Path(manifest_path or MANIFEST).resolve().parent
    return (Path(agent_root or data_root / "agent-input"),
            Path(grader_root or data_root / "grader-only"))


def _find_manifest_path(manifest, results_dir, manifest_path=None):
    if manifest_path is not None:
        path = Path(manifest_path).resolve()
        load_manifest(path)
        return path
    candidates = {MANIFEST.resolve()}
    for parent in [Path(results_dir).resolve(), *Path(results_dir).resolve().parents]:
        candidates.add(parent / "manifest.json")
    candidates.update(path.resolve() for path in ROOT.parent.glob("dataset_*/data/manifest.json"))
    matches = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            on_disk = load_manifest(path)
        except SystemExit:
            continue
        if on_disk == manifest:
            matches.append(path)
    if len(matches) != 1:
        raise SystemExit("cannot uniquely locate the checksummed manifest for this results directory")
    return matches[0]


def _validate_case_artifacts(record, agent_dir, grader_dir):
    if not agent_dir.is_dir() or not grader_dir.is_dir():
        return "agent-input or grader-only case directory is missing"
    try:
        agent_files = tree_sha256(agent_dir)
        grader_files = tree_sha256(grader_dir)
    except (OSError, ValueError) as exc:
        return "case tree cannot be hashed: {0}".format(exc)

    frozen = record.get("artifact_sha256")
    if frozen:
        if agent_files != frozen.get("agent_input"):
            return "agent-input tree SHA-256 does not match the manifest"
        if grader_files != frozen.get("grader_only"):
            return "grader-only tree SHA-256 does not match the manifest"
        return None

    # Legacy manifests record individual source/oracle hashes but not whole trees.
    source_hashes = record.get("source_sha256", {}).get("files", {})
    existing = record.get("existing_test_manifest", {})
    oracle = record.get("oracle_manifest", {})
    expected_agent = set(source_hashes) | {"test_existing.py", "task.json"}
    expected_grader = {"test_acceptance.py", "reference.patch", "labels.json"}
    if set(agent_files) != expected_agent or set(grader_files) != expected_grader:
        return "legacy case tree has missing or unexpected files"
    if any(agent_files.get(name) != digest for name, digest in source_hashes.items()):
        return "source SHA-256 does not match the manifest"
    if agent_files.get("test_existing.py") != existing.get("sha256"):
        return "existing test SHA-256 does not match the manifest"
    if grader_files.get("test_acceptance.py") != oracle.get("acceptance_sha256"):
        return "acceptance test SHA-256 does not match the manifest"
    if grader_files.get("reference.patch") != record.get("reference_patch_hash"):
        return "reference patch SHA-256 does not match the manifest"
    try:
        task = json.loads((agent_dir / "task.json").read_text(encoding="utf-8"))
        labels = json.loads((grader_dir / "labels.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "task or grader labels are not valid JSON"
    task_expected = {
        "case_id": record["case_id"],
        "dataset_version": record["dataset_version"],
        "title": record["title"],
        "report": record["report"],
        "contract": record["expected_behavior"],
        "allowed_paths": record["allowed_paths"],
        "protected_paths": record["protected_paths"],
        "split": record["split"],
    }
    if any(task.get(key) != value for key, value in task_expected.items()):
        return "agent task description does not match the manifest"
    probe_names = [item["name"] for item in oracle.get("mutation_probes", [])]
    labels_expected = {
        "case_id": record["case_id"],
        "contract": record["expected_behavior"],
        "mutations": probe_names,
        "split": record["split"],
        "group_id": record["group_id"],
    }
    if any(labels.get(key) != value for key, value in labels_expected.items()):
        return "grader labels do not match the manifest"
    return None


def find_case(manifest, case_id):
    for record in manifest["cases"]:
        if record["case_id"] == case_id:
            return record
    raise SystemExit("unknown case_id: {0}".format(case_id))


def changed_paths(patch_text):
    """Return the paths a unified diff touches, or ``None`` if it is malformed."""
    if "GIT binary patch" in patch_text or "Binary files" in patch_text:
        return None
    paths = []
    saw_header = False
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            saw_header = True
            target = line[4:].strip().split("\t")[0]
            if target != "/dev/null":
                paths.append(_strip_prefix(target))
        elif line.startswith("+++ "):
            target = line[4:].strip().split("\t")[0]
            if target != "/dev/null":
                paths.append(_strip_prefix(target))
    if not saw_header:
        return None
    return sorted(set(paths))


def _strip_prefix(path):
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _gate(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": detail}


def _clean(record):
    return {
        "tests": record.get("tests"),
        "failures": record.get("failures"),
        "errors": record.get("errors"),
        "skipped": record.get("skipped"),
        "exit_code": record.get("exit_code"),
        "timed_out": record.get("timed_out", False),
        "container_cleanup": record.get("container_cleanup", False),
    }


# --------------------------------------------------------------------------- #
# scoring one case
# --------------------------------------------------------------------------- #
def score_case(case_id, patch_text, run_id, manifest=None, agent_root=None,
               grader_root=None, manifest_path=None):
    """Score one candidate patch.

    ``agent_root`` / ``grader_root`` / ``manifest_path`` let a second dataset
    (dataset_v2) reuse the same hard gates while keeping its own frozen tree.
    """
    frozen_manifest = load_manifest(manifest_path)
    if manifest is not None and manifest != frozen_manifest:
        raise SystemExit("provided manifest object differs from the checksummed manifest file")
    manifest = frozen_manifest
    record = find_case(manifest, case_id)
    agent_base, grader_base = _case_roots(manifest_path, agent_root, grader_root)
    agent_dir = agent_base / case_id
    grader_dir = grader_base / case_id

    result = {
        "run_id": run_id,
        "case_id": case_id,
        "dataset_version": manifest["dataset_version"],
        "manifest_sha256": sha256_file(Path(manifest_path or MANIFEST).resolve()),
        "patch_sha256": sha256_text(patch_text),
        "scored_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "split": record["split"],
        "category": None,
        "passed": False,
        "gates": [],
        "tests": {},
        "changed_paths": [],
        "cost": {"tokens": None, "usd": None, "note": "评分器只看到补丁文本，用量由上游运行器提供"},
    }

    def finish(category):
        result["category"] = category
        result["passed"] = category is None
        return result

    artifact_error = _validate_case_artifacts(record, agent_dir, grader_dir)
    result["gates"].append(_gate(
        "frozen_case_intact", artifact_error is None,
        artifact_error or "case files and directory membership match the frozen manifest"))
    if artifact_error:
        return finish("oracle_invalid")

    if not patch_text.strip():
        result["gates"].append(_gate("candidate_present", False, "补丁为空"))
        return finish("no_candidate")
    result["gates"].append(_gate("candidate_present", True, "非空补丁 {0} 字节".format(len(patch_text))))

    paths = changed_paths(patch_text)
    if not paths:
        result["gates"].append(_gate("patch_parseable", False, "无法从 diff 头部解析出被改文件"))
        return finish("patch_format")
    result["changed_paths"] = paths
    result["gates"].append(_gate("patch_parseable", True, "改动 {0} 个文件".format(len(paths))))

    allowed = set(record["allowed_paths"])
    protected = set(record["protected_paths"])
    touched_protected = sorted(protected & set(paths))
    outside = sorted(set(paths) - allowed - protected)
    result["gates"].append(_gate(
        "scope_allowed", not outside,
        "越界文件: {0}".format(outside) if outside else "全部位于 allowed_paths"))
    result["gates"].append(_gate(
        "tests_untouched", not touched_protected,
        "触碰受保护文件: {0}".format(touched_protected) if touched_protected else "未触碰受保护文件"))
    if touched_protected:
        return finish("test_tampering")
    if outside:
        return finish("scope_violation")

    work = Path(tempfile.mkdtemp(prefix="dv1-score-{0}-".format(case_id)))
    try:
        shutil.copytree(str(agent_dir), str(work), dirs_exist_ok=True)
        (work / "test_acceptance.py").write_text(
            (grader_dir / "test_acceptance.py").read_text(encoding="utf-8"),
            encoding="utf-8", newline="\n")

        applied = apply_patch(work, patch_text)
        result["gates"].append(_gate("patch_applies", applied["ok"], applied["stderr"] or "git apply 成功"))
        if not applied["ok"]:
            return finish("patch_apply")

        expected_existing = record["existing_test_manifest"]["sha256"]
        if sha256_file(work / "test_existing.py") != expected_existing:
            result["gates"].append(_gate("existing_tests_unchanged", False, "test_existing.py 哈希变化"))
            return finish("test_tampering")
        result["gates"].append(_gate("existing_tests_unchanged", True, "test_existing.py 哈希一致"))

        existing = run_tests(work, "test_existing.py")
        acceptance = run_tests(work, "test_acceptance.py")
        result["tests"] = {"existing": _clean(existing), "acceptance": _clean(acceptance)}

        cleanup_ok = (existing.get("container_cleanup") is True
                      and acceptance.get("container_cleanup") is True)
        result["gates"].append(_gate(
            "containers_cleaned", cleanup_ok,
            "测试容器未能确认清理" if not cleanup_ok else "两个测试容器均已清理"))
        if not cleanup_ok:
            return finish("infrastructure")

        if existing["timed_out"] or acceptance["timed_out"]:
            result["gates"].append(_gate("tests_completed", False, "测试超时"))
            return finish("timeout_or_budget")
        expected_existing = record["existing_test_manifest"]["expected_tests"]
        expected_acceptance = record["oracle_manifest"]["expected_tests"]
        tests_complete = (
            existing["tests"] == expected_existing
            and acceptance["tests"] == expected_acceptance
            and existing["skipped"] == 0 and acceptance["skipped"] == 0
            and existing["exit_code"] is not None and acceptance["exit_code"] is not None
            and existing.get("container_cleanup") is True
            and acceptance.get("container_cleanup") is True
        )
        result["gates"].append(_gate(
            "tests_complete", tests_complete,
            "收集数或跳过数与冻结 manifest 不符"
            if not tests_complete else "收集数量符合预期且无跳过"))
        if not tests_complete:
            result["gates"].append(_gate("tests_completed", False, "测试未完整执行"))
            return finish("infrastructure")
        if ((existing["exit_code"] == 0 and (existing["failures"] or existing["errors"]))
                or (acceptance["exit_code"] == 0 and (acceptance["failures"] or acceptance["errors"]))
                or (existing["exit_code"] != 0 and not (existing["failures"] or existing["errors"]))
                or (acceptance["exit_code"] != 0
                    and not (acceptance["failures"] or acceptance["errors"]))):
            result["gates"].append(_gate(
                "test_runner_consistent", False,
                "JUnit 结果与 pytest 退出码不一致，不能判定候选"))
            return finish("infrastructure")
        result["gates"].append(_gate("test_runner_consistent", True, "JUnit 与 pytest 退出码一致"))

        regression_ok = (existing["failures"] == 0 and existing["errors"] == 0
                         and existing["exit_code"] == 0)
        result["gates"].append(_gate(
            "no_regression", regression_ok,
            "既有测试失败 {0} / 错误 {1}".format(existing["failures"], existing["errors"])))
        if not regression_ok:
            return finish("regression")

        acceptance_ok = (acceptance["failures"] == 0 and acceptance["errors"] == 0
                         and acceptance["exit_code"] == 0)
        result["gates"].append(_gate(
            "defect_fixed", acceptance_ok,
            "验收测试失败 {0} / 错误 {1}".format(acceptance["failures"], acceptance["errors"])))
        if acceptance_ok:
            return finish(None)
        attempted = (acceptance["tests"] or 0) - (acceptance["failures"] or 0) - (acceptance["errors"] or 0)
        return finish("incomplete_fix" if attempted > 0 else "wrong_behavior")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def wilson_interval(successes, total, z=1.96):
    if total == 0:
        return (None, None)
    ratio = successes / total
    denominator = 1 + z * z / total
    centre = (ratio + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(ratio * (1 - ratio) / total + z * z / (4 * total * total)) / denominator
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def summarize(results_dir, manifest=None):
    results_dir = Path(results_dir)
    manifest = manifest or load_manifest()
    manifest_path = _find_manifest_path(manifest, results_dir)
    manifest_digest = sha256_file(manifest_path)
    files = sorted(results_dir.glob("*.json"))
    if not files:
        raise SystemExit("no result json found in {0}".format(results_dir))

    try:
        results = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("result file is unreadable or invalid JSON: {0}".format(exc)) from exc
    records = {item["case_id"]: item for item in manifest["cases"]}
    by_case = {}
    run_ids = set()
    for result in results:
        case_id = result.get("case_id")
        if case_id not in records:
            raise SystemExit("result contains an unregistered case_id: {0}".format(case_id))
        if case_id in by_case:
            raise SystemExit("multiple candidates for {0}; pass@1 requires one result per case".format(case_id))
        if result.get("manifest_sha256") != manifest_digest:
            raise SystemExit("result manifest SHA-256 does not match the frozen dataset: {0}".format(case_id))
        case_record = records[case_id]
        if (result.get("dataset_version") != manifest["dataset_version"]
                or result.get("split") != case_record["split"]):
            raise SystemExit("result dataset version or split does not match the manifest: {0}".format(case_id))
        patch_digest = result.get("patch_sha256")
        if (not isinstance(patch_digest, str) or len(patch_digest) != 64
                or any(char not in "0123456789abcdef" for char in patch_digest)):
            raise SystemExit("result patch SHA-256 is missing or invalid: {0}".format(case_id))
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise SystemExit("result has no run_id: {0}".format(case_id))
        run_ids.add(run_id)
        category = result.get("category")
        if category is not None and category not in CATEGORIES:
            raise SystemExit("result has an unknown category: {0}".format(category))
        if result.get("passed") is not (category is None):
            raise SystemExit("result passed/category fields conflict: {0}".format(case_id))
        if category is None:
            gates = result.get("gates") or []
            gate_map = {gate.get("name"): gate.get("ok") for gate in gates if isinstance(gate, dict)}
            required_gates = {
                "frozen_case_intact", "candidate_present", "patch_parseable", "scope_allowed",
                "tests_untouched", "patch_applies", "existing_tests_unchanged", "containers_cleaned",
                "tests_complete", "test_runner_consistent", "no_regression", "defect_fixed",
            }
            tests = result.get("tests") or {}
            expected = case_record
            full_tests = all(
                tests.get(kind, {}).get("tests") == expected[test_key]["expected_tests"]
                and tests.get(kind, {}).get("failures") == 0
                and tests.get(kind, {}).get("errors") == 0
                and tests.get(kind, {}).get("skipped") == 0
                and tests.get(kind, {}).get("exit_code") == 0
                and not tests.get(kind, {}).get("timed_out", False)
                and tests.get(kind, {}).get("container_cleanup") is True
                for kind, test_key in (("existing", "existing_test_manifest"),
                                       ("acceptance", "oracle_manifest"))
            )
            if (not required_gates.issubset(gate_map)
                    or not all(gate_map[name] is True for name in required_gates)
                    or not full_tests):
                raise SystemExit("result claims pass without complete, unskipped tests: {0}".format(case_id))
        by_case[case_id] = result
    if len(run_ids) > 1:
        raise SystemExit("results mix multiple run_id values; summarize one evaluation run at a time")

    denominator = manifest["case_count"]
    passed = [item for item in by_case.values() if item["passed"]]
    categories = {}
    for item in by_case.values():
        key = item["category"] or "pass"
        categories[key] = categories.get(key, 0) + 1
    missing_cases = denominator - len(by_case)
    if missing_cases:
        categories["unscored"] = missing_cases

    def stage_counts(gate_name):
        attempted = [item for item in by_case.values()
                     if any(gate.get("name") == gate_name for gate in item.get("gates", []))]
        passed_count = sum(
            1 for item in attempted
            if any(gate.get("name") == gate_name and gate.get("ok") is True
                   for gate in item.get("gates", []))
        )
        return passed_count, len(attempted)

    def ratio(numerator, total, label):
        low, high = wilson_interval(numerator, total)
        return {"metric": label, "numerator": numerator, "denominator": total,
                "rate": round(numerator / total, 4) if total else None,
                "ci95": [low, high]}

    patch_outputs = sum(
        1 for item in by_case.values()
        if any(gate.get("name") == "candidate_present" and gate.get("ok") is True
               for gate in item.get("gates", []))
    )
    parsed, parse_attempts = stage_counts("patch_parseable")
    applied, apply_attempts = stage_counts("patch_applies")
    regression_passed, regression_attempts = stage_counts("no_regression")
    defect_fixed, defect_attempts = stage_counts("defect_fixed")
    scope_passed, scope_attempts = stage_counts("scope_allowed")
    metrics = [
        ratio(len(passed), denominator, "pass@1"),
        ratio(patch_outputs, denominator, "patch_output_rate"),
        ratio(parsed, parse_attempts, "patch_parse_rate"),
        ratio(applied, apply_attempts, "patch_apply_rate"),
        ratio(regression_passed, regression_attempts, "regression_pass_rate"),
        ratio(defect_fixed, defect_attempts, "defect_fix_rate"),
        ratio(scope_passed, scope_attempts, "scope_compliance_rate"),
    ]

    summary = {
        "results_dir": str(results_dir),
        "dataset_version": manifest["dataset_version"],
        "run_id": next(iter(run_ids), None),
        "manifest_sha256": manifest_digest,
        "scored_cases": len(by_case),
        "registered_cases": denominator,
        "missing_cases": missing_cases,
        "pass_at_1": metrics[0]["rate"],
        "metrics": metrics,
        "failure_categories": dict(sorted(categories.items())),
        "note": "pass@1 分母为全部注册题，缺失按失败；阶段指标只统计实际到达该门槛的候选，重复题和混合 run_id 拒绝汇总",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def read_patch(path):
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score a candidate patch for dataset v1.")
    parser.add_argument("--case", help="case id, e.g. dv1-01")
    parser.add_argument("--patch", help="path to a unified diff, or - for stdin")
    parser.add_argument("--run-id", default="run-local")
    parser.add_argument("--out", help="where to write the machine readable result")
    parser.add_argument("--summarize", help="aggregate a results directory instead of scoring")
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)

    if args.summarize:
        summarize(args.summarize, manifest)
        return 0

    if not args.case or not args.patch:
        parser.error("--case and --patch are required unless --summarize is used")

    result = score_case(args.case, read_patch(args.patch), args.run_id, manifest)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8", newline="\n")
    print(payload)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    code = main()
    sys.exit(code)
