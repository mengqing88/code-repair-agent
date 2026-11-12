"""Validate incoming synthetic fixtures and build a namespaced candidate index."""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
OUTPUT = INCOMING / "normalized-candidate-v1"
REPO_ROOT = ROOT.parent
TEMP_ROOT = REPO_ROOT / "work" / "incoming-validation"
ALLOWED_IMPORTS = {"datetime", "decimal", "itertools", "math", "pytest", "re", "target", "time"}
FORBIDDEN_CALLS = {
    "__import__", "breakpoint", "compile", "eval", "exec", "getattr", "globals",
    "input", "locals", "open", "setattr", "vars",
}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def digest(data: bytes) -> str:
    """Return a SHA-256 checksum for immutable input or evidence bytes."""
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    """Hash one source artifact without changing it."""
    return digest(path.read_bytes())


def source_manifest(group: Path) -> list[dict[str, str]]:
    """Hash source files and reject symbolic links or paths escaping the source group."""
    group_root = group.resolve()
    entries = []
    for path in sorted(group.rglob("*"), key=lambda item: item.relative_to(group).as_posix()):
        if path.is_symlink():
            raise ValueError(f"评测来源不允许符号链接: {path}")
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(group_root)
        except ValueError:
            raise ValueError(f"评测文件越出来源目录: {path}") from None
        entries.append({"path": relative.as_posix(), "sha256": file_digest(path)})
    return entries


def is_safe_identifier(value: object) -> bool:
    """Return whether an identifier can safely be used as one filename or directory name."""
    return isinstance(value, str) and SAFE_ID.fullmatch(value) is not None and value not in {".", ".."}


def read_json(path: Path):
    """Parse one UTF-8 JSON input and include its path in data errors."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 JSON: {path}: {type(error).__name__}") from None


def read_relevance(path: Path) -> tuple[dict, str | None]:
    """Read either a direct relevance map or a map wrapped with generator metadata."""
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"相关性文件必须是 JSON 对象: {path}")
    if "labels" in value:
        if set(value) - {"labels", "annotation"}:
            raise ValueError(f"相关性文件存在未知顶层字段: {path}")
        labels = value["labels"]
        annotation = value.get("annotation")
        if annotation is not None and not isinstance(annotation, str):
            raise ValueError(f"相关性说明必须是字符串: {path}")
    else:
        labels = value
        annotation = None
    if not isinstance(labels, dict):
        raise ValueError(f"相关性标签必须是 JSON 对象: {path}")
    return labels, annotation


def write_json_once(path: Path, value) -> None:
    """Create a normalized artifact without overwriting an earlier audit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def _source_path(line: str, marker: str) -> str | None:
    """Extract a path from a unified-diff file header."""
    if not line.startswith(marker):
        return None
    value = line[len(marker):].split("\t", 1)[0].strip()
    if value == "/dev/null":
        return value
    prefix = "a/" if marker == "--- " else "b/"
    if not value.startswith(prefix):
        raise ValueError(f"补丁头缺少 {prefix} 前缀: {value}")
    relative = Path(value[2:])
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise ValueError(f"补丁路径越界: {value}")
    return relative.as_posix()


def normalize_patch(patch: str) -> tuple[str, list[str]]:
    """Normalize the known history filename mismatch and require target.py only."""
    lines = patch.splitlines(keepends=True)
    paths = []
    normalized = []
    changes = []
    for line in lines:
        old = _source_path(line, "--- ")
        new = _source_path(line, "+++ ")
        if old is not None:
            paths.append(old)
        if new is not None:
            paths.append(new)
        if line.startswith(("--- a/solution.py", "+++ b/solution.py")):
            line = line.replace("solution.py", "target.py", 1)
            changes.append("solution.py -> target.py")
        normalized.append(line)
    normalized_patch = "".join(normalized)
    normalized_paths = []
    for line in normalized_patch.splitlines():
        path = _source_path(line, "--- ")
        if path is None:
            path = _source_path(line, "+++ ")
        if path not in (None, "/dev/null"):
            normalized_paths.append(path)
    if not paths or not normalized_paths or set(normalized_paths) != {"target.py"}:
        raise ValueError(f"补丁必须只修改 target.py，实际路径={sorted(set(normalized_paths or paths))}")
    return normalized_patch, sorted(set(changes))


def check_python_inputs(paths: list[Path]) -> None:
    """Reject imports and syntax outside the reviewed synthetic-fixture subset."""
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as error:
            raise ValueError(f"Python 样例无法静态解析: {path.name}: {type(error).__name__}") from None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".", 1)[0]]
            else:
                names = []
            blocked = sorted(set(names) - ALLOWED_IMPORTS)
            if blocked:
                raise ValueError(f"样例导入未经允许的模块 {blocked}: {path.name}")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise ValueError(f"样例访问双下划线属性 {node.attr}: {path.name}")
            if isinstance(node, ast.Name) and node.id == "__builtins__":
                raise ValueError(f"样例访问受限名称 {node.id}: {path.name}")
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name) and function.id in FORBIDDEN_CALLS:
                    raise ValueError(f"样例调用受限内置函数 {function.id}: {path.name}")


def pytest_run(directory: Path, test_targets: list[str], run_name: str) -> dict:
    """Run reviewed fixtures in a temporary directory and parse JUnit counts."""
    junit = directory / f"{run_name}.xml"
    config = directory / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8", newline="\n")
    command = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "pytest", "-q",
               "-c", str(config), "-p", "no:cacheprovider", f"--junitxml={junit}", *test_targets]
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=45)
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "timed_out": True, "tests": {}, "stdout_tail": "", "stderr_tail": ""}
    result = {"exit_code": completed.returncode, "timed_out": False, "tests": {},
              "stdout_tail": completed.stdout[-1200:], "stderr_tail": completed.stderr[-1200:]}
    if not junit.is_file():
        result["xml_error"] = "pytest 没有生成 JUnit 证据"
        return result
    try:
        root = ET.parse(junit).getroot()
    except ET.ParseError:
        result["xml_error"] = "JUnit 证据无法解析"
        return result
    counts = {}
    suites = root.findall(".//testsuite") if root.tag != "testsuite" else [root]
    for suite in suites:
        for test in suite.findall("testcase"):
            classname = test.attrib.get("classname", "")
            module = next((name for name in ("test_existing", "test_acceptance", "test_verification")
                           if name in classname), "unknown")
            item = counts.setdefault(module, {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0})
            item["total"] += 1
            if test.find("failure") is not None:
                item["failed"] += 1
            elif test.find("error") is not None:
                item["errors"] += 1
            elif test.find("skipped") is not None:
                item["skipped"] += 1
            else:
                item["passed"] += 1
    result["tests"] = counts
    result["unclassified_tests"] = counts.get("unknown", {}).get("total", 0)
    result["collection_errors"] = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    return result


def _all_pass(counts: dict) -> bool:
    """Check that a test group ran and every collected case passed."""
    return counts.get("total", 0) > 0 and counts["passed"] == counts["total"] and not any(
        counts[name] for name in ("failed", "errors", "skipped"))


def _task_fixture(group: Path, case_dir: Path, temp_dir: Path) -> tuple[dict, dict]:
    """Run the case baseline and reference patch in a fresh F-drive workspace."""
    task = read_json(case_dir / "task.json")
    case_id = task.get("case_id")
    if (not is_safe_identifier(case_id) or case_id != case_dir.name
            or task.get("allowed_paths") != ["target.py"]):
        raise ValueError(f"task 身份或 allowed_paths 不一致: {case_dir}")
    if not all(isinstance(task.get(field), str) and task[field].strip()
               for field in ("title", "report", "contract")):
        raise ValueError(f"task 缺少标题、报告或行为契约: {case_dir}")
    sample_files = [case_dir / name for name in ("target.py", "test_existing.py")]
    oracle_dir = group / "oracles" / case_id
    acceptance = oracle_dir / "test_acceptance.py"
    patch_file = oracle_dir / "reference.patch"
    if not all(path.is_file() for path in (*sample_files, acceptance, patch_file)):
        raise ValueError(f"样例缺少代码、测试或参考补丁: {case_id}")
    patch_text = patch_file.read_text(encoding="utf-8")
    patch_text, normalizations = normalize_patch(patch_text)
    check_python_inputs([*sample_files, acceptance])
    working = temp_dir / case_id
    working.mkdir()
    for source in sample_files:
        shutil.copyfile(source, working / source.name)
    shutil.copyfile(acceptance, working / acceptance.name)
    test_hashes = {name: file_digest(working / name) for name in ("test_existing.py", "test_acceptance.py")}
    baseline = pytest_run(working, ["test_existing.py", "test_acceptance.py"], "baseline")
    baseline_existing = baseline.get("tests", {}).get("test_existing", {})
    baseline_acceptance = baseline.get("tests", {}).get("test_acceptance", {})
    baseline_ok = (_all_pass(baseline_existing) and baseline_acceptance.get("total", 0) > 0
                   and baseline_acceptance.get("failed", 0) > 0
                   and baseline_acceptance.get("errors", 0) == 0
                   and baseline_acceptance.get("skipped", 0) == 0
                   and baseline.get("collection_errors", 0) == 0
                   and baseline.get("unclassified_tests", 0) == 0
                   and baseline.get("exit_code") != 0)
    temporary_patch = working / "reference.patch"
    temporary_patch.write_text(patch_text, encoding="utf-8", newline="\n")
    checked = subprocess.run([shutil.which("git") or "git", "apply", "--check", "-p1", str(temporary_patch)],
                             cwd=working, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if checked.returncode:
        raise ValueError(f"参考补丁无法应用: {case_id}: {checked.stderr[-500:]}")
    before_hash = file_digest(working / "target.py")
    applied = subprocess.run([shutil.which("git") or "git", "apply", "-p1", str(temporary_patch)],
                             cwd=working, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if applied.returncode:
        raise ValueError(f"参考补丁应用失败: {case_id}: {applied.stderr[-500:]}")
    after_hash = file_digest(working / "target.py")
    fixed = pytest_run(working, ["test_existing.py", "test_acceptance.py"], "fixed")
    fixed_ok = (_all_pass(fixed.get("tests", {}).get("test_existing", {}))
                and _all_pass(fixed.get("tests", {}).get("test_acceptance", {}))
                and fixed.get("collection_errors", 0) == 0
                and fixed.get("unclassified_tests", 0) == 0
                and fixed.get("exit_code") == 0)
    tests_unchanged = all(file_digest(working / name) == checksum for name, checksum in test_hashes.items())
    result = {
        "case_id": case_id,
        "case_key": f"{group.name}::{case_id}",
        "expected_behavior": "repair",
        "defect_family": "unclassified_pending_manual_annotation",
        "split": "unassigned",
        "review_status": "execution_checked_not_independently_reviewed",
        "status": "passed" if baseline_ok and fixed_ok and tests_unchanged and before_hash != after_hash else "failed",
        "baseline": baseline,
        "fixed": fixed,
        "reference_patch_sha256": digest(patch_text.encode("utf-8")),
        "source_reference_patch_sha256": file_digest(patch_file),
        "normalizations": normalizations,
        "source_hashes": {"task": file_digest(case_dir / "task.json"),
                          "target": file_digest(case_dir / "target.py"),
                          "existing_test": file_digest(case_dir / "test_existing.py"),
                          "acceptance_test": file_digest(acceptance),
                          "reference_patch": file_digest(patch_file)},
        "test_files_unchanged": tests_unchanged,
        "reference_changed_target": before_hash != after_hash,
    }
    return task, result


def _history_fixture(group: Path, history_file: Path, temp_dir: Path) -> tuple[dict, dict, str]:
    """Test one synthetic history example before and after its normalized patch."""
    history = read_json(history_file)
    experience_id = history.get("experience_id")
    source = history.get("before_source")
    tests = history.get("verification_tests")
    patch = history.get("patch_diff")
    if (not is_safe_identifier(experience_id) or experience_id != history_file.stem
            or not isinstance(source, str) or not source.strip()
            or not isinstance(tests, str) or not tests.strip() or not isinstance(patch, str) or not patch.strip()):
        raise ValueError(f"历史经验 ID 不安全、不匹配或缺少 source/tests/patch: {history_file}")
    patch, normalizations = normalize_patch(patch)
    working = temp_dir / experience_id
    working.mkdir()
    target = working / "target.py"
    test_file = working / "test_verification.py"
    target.write_text(source, encoding="utf-8", newline="\n")
    test_file.write_text(tests, encoding="utf-8", newline="\n")
    check_python_inputs([target, test_file])
    test_hash = file_digest(test_file)
    before = pytest_run(working, [test_file.name], "before")
    before_counts = before.get("tests", {}).get("test_verification", {})
    before_ok = (before_counts.get("total", 0) > 0 and before_counts.get("failed", 0) > 0
                 and before_counts.get("errors", 0) == 0 and before_counts.get("skipped", 0) == 0
                 and before.get("collection_errors", 0) == 0
                 and before.get("unclassified_tests", 0) == 0
                 and before.get("exit_code") != 0)
    patch_file = working / "experience.patch"
    patch_file.write_text(patch, encoding="utf-8", newline="\n")
    checked = subprocess.run([shutil.which("git") or "git", "apply", "--check", "-p1", str(patch_file)],
                             cwd=working, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if checked.returncode:
        raise ValueError(f"历史补丁无法应用: {experience_id}: {checked.stderr[-500:]}")
    before_hash = file_digest(target)
    applied = subprocess.run([shutil.which("git") or "git", "apply", "-p1", str(patch_file)],
                             cwd=working, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if applied.returncode:
        raise ValueError(f"历史补丁应用失败: {experience_id}: {applied.stderr[-500:]}")
    after_hash = file_digest(target)
    fixed = pytest_run(working, [test_file.name], "after")
    fixed_counts = fixed.get("tests", {}).get("test_verification", {})
    fixed_ok = (_all_pass(fixed_counts) and fixed.get("collection_errors", 0) == 0
                and fixed.get("unclassified_tests", 0) == 0
                and fixed.get("exit_code") == 0 and file_digest(test_file) == test_hash)
    result = {
        "experience_id": experience_id,
        "experience_key": f"{group.name}::{experience_id}",
        "provenance_status": history.get("provenance", "not_recorded"),
        "review_status": "execution_checked_not_independently_reviewed",
        "status": "passed" if before_ok and fixed_ok and before_hash != after_hash else "failed",
        "before": before,
        "after": fixed,
        "source_sha256": file_digest(history_file),
        "normalized_patch_sha256": digest(patch.encode("utf-8")),
        "normalizations": normalizations,
        "verification_tests_unchanged": file_digest(test_file) == test_hash,
        "patch_changed_target": before_hash != after_hash,
    }
    history = {**history, "dataset_id": group.name,
               "dataset_experience_id": f"{group.name}::{experience_id}",
               "patch_diff": patch,
               "normalization_notes": normalizations}
    return history, result, experience_id


def _validate_group(group: Path, workers: int) -> dict:
    """Validate every case/history fixture in one source collection."""
    sample_root, oracle_root, history_root = (group / "sample_repo", group / "oracles", group / "history_cases")
    case_dirs = sorted((path for path in sample_root.iterdir() if path.is_dir()), key=lambda path: path.name)
    history_files = sorted(history_root.glob("*.json"), key=lambda path: path.name)
    if not case_dirs or not history_files or not oracle_root.is_dir():
        raise ValueError(f"来源目录结构不完整: {group.name}")
    relevance, relevance_annotation = read_relevance(group / "relevance.json")
    original_manifest = source_manifest(group)
    original_manifest_hash = digest(json.dumps(original_manifest, ensure_ascii=False,
                                               sort_keys=True, separators=(",", ":")).encode("utf-8"))
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{group.name}-", dir=TEMP_ROOT))
    cases, histories = [], []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_task_fixture, group, case_dir, temp_dir) for case_dir in case_dirs]
            for future in as_completed(futures):
                task, result = future.result()
                result["relevant_experience_keys"] = [f"{group.name}::{item}" for item in
                    relevance.get(task["case_id"], [])]
                cases.append(result)
            futures = [pool.submit(_history_fixture, group, history_file, temp_dir) for history_file in history_files]
            normalized_histories = []
            for future in as_completed(futures):
                normalized, result, _ = future.result()
                normalized_histories.append(normalized)
                histories.append(result)
        cases.sort(key=lambda item: item["case_id"])
        histories.sort(key=lambda item: item["experience_id"])
        normalized_histories.sort(key=lambda item: item["dataset_experience_id"])
        case_ids = {item["case_id"] for item in cases}
        history_ids = {item["experience_id"] for item in normalized_histories}
        relevance_ok = (set(relevance) == case_ids and all(
            isinstance(values, list)
            and all(isinstance(item, str) for item in values)
            and len(values) == len(set(values))
            and set(values) <= history_ids
            for values in relevance.values()))
        if source_manifest(group) != original_manifest:
            raise ValueError(f"验证期间来源文件发生变化: {group.name}")
        return {
            "dataset_id": group.name,
            "source_directory": group.relative_to(INCOMING).as_posix(),
            "source_manifest": original_manifest,
            "source_manifest_sha256": original_manifest_hash,
            "relevance_annotation": relevance_annotation,
            "provenance_status": "generated_candidate_unreviewed",
            "split": "unassigned",
            "formal_eligible": False,
            "eligibility_blockers": [
                "来源材料未记录可核验的独立人工复核者",
                "缺少开发/验证/冻结测试划分及跨来源近重复族审核",
                "relevance 标签由样例作者指定，尚未独立复核",
                "样例为生成的合成类比，不能代表真实生产缺陷",
            ],
            "relevance_integrity": {"passed": relevance_ok, "entries": len(relevance)},
            "case_count": len(cases),
            "history_count": len(histories),
            "cases": cases,
            "histories": histories,
            "normalized_histories": normalized_histories,
            "passed_cases": sum(item["status"] == "passed" for item in cases),
            "passed_histories": sum(item["status"] == "passed" for item in histories),
        }
    finally:
        shutil.rmtree(temp_dir)


def build_candidate_index(output: Path, workers: int) -> dict:
    """Run every source suite and write a hash-bound, namespaced candidate set."""
    if output.exists():
        raise FileExistsError(f"输出已存在，避免覆盖旧评测证据: {output}")
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    git = subprocess.run([shutil.which("git") or "git", "--version"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
    pytest_version = subprocess.run([sys.executable, "-I", "-m", "pytest", "--version"], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
    source_groups = sorted((path for path in INCOMING.iterdir() if path.is_dir()
                            and (path / "sample_repo").is_dir()), key=lambda path: path.name)
    if not source_groups:
        raise ValueError(f"没有可审计的数据来源: {INCOMING}")
    groups = [_validate_group(group, workers) for group in source_groups]
    cases = [case for group in groups for case in group["cases"]]
    histories = [history for group in groups for history in group["histories"]]
    case_keys = [case["case_key"] for case in cases]
    experience_keys = [history["experience_key"] for history in histories]
    if len(case_keys) != len(set(case_keys)) or len(experience_keys) != len(set(experience_keys)):
        raise ValueError("来源命名空间后仍存在重复 case/experience ID")
    success = all(group["passed_cases"] == group["case_count"]
                  and group["passed_histories"] == group["history_count"]
                  and group["relevance_integrity"]["passed"] for group in groups)
    index = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_standard": "评测要求.md v1.0",
        "dataset_id": "incoming-namespaced-candidate-v1",
        "status": "structurally_and_execution_validated_candidate" if success else "validation_failed",
        "formal_eligible": False,
        "cross_source_near_duplicate_review": "not_performed",
        "formal_eligibility_reasons": sorted({reason for group in groups for reason in group["eligibility_blockers"]}),
        "environment": {"python": sys.version, "pytest": pytest_version, "platform": platform.platform(), "git": git},
        "execution_isolation": "temporary per-case directories, Python isolated mode, and pytest plugin autoload disabled; Docker Engine was unavailable, so this was not an OS-level sandbox",
        "security_note": "486 Python fixture files were statically reviewed for allowed imports, dynamic execution, dunder access, and symlinks before execution; this static review is not a security boundary",
        "source_manifest_sha256": digest(json.dumps(
            [{"dataset_id": group["dataset_id"], "sha256": group["source_manifest_sha256"]}
             for group in groups], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")),
        "count": {"source_groups": len(groups), "cases": len(cases), "histories": len(histories)},
        "groups": groups,
    }
    output.mkdir(parents=True)
    normalized_root = output / "history_cases"
    relevance_index = {}
    for group in groups:
        for history in group["normalized_histories"]:
            filename = history["experience_id"] + ".json"
            write_json_once(normalized_root / group["dataset_id"] / filename, history)
        relevance, _ = read_relevance(INCOMING / group["source_directory"] / "relevance.json")
        relevance_index.update({f"{group['dataset_id']}::{case_id}":
                                [f"{group['dataset_id']}::{experience_id}" for experience_id in values]
                                for case_id, values in relevance.items()})
        del group["normalized_histories"]
    write_json_once(output / "relevance.json", relevance_index)
    write_json_once(output / "index.json", index)
    return index


def main(argv=None) -> int:
    """Expose a repeatable incoming dataset audit and normalized output build."""
    parser = argparse.ArgumentParser(description="验证 incoming 样例并生成 namespaced 候选清单")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="新建输出目录；已有目录会拒绝覆盖")
    parser.add_argument("--workers", type=int, default=4, help="并发运行独立临时样例的工作线程数")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("--workers 必须在 1 到 16 之间")
    try:
        result = build_candidate_index(args.output.resolve(), args.workers)
    except Exception as error:
        print(f"incoming 验证失败: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output.resolve()), "status": result["status"],
                      "count": result["count"],
                      "passed_cases": sum(group["passed_cases"] for group in result["groups"]),
                      "passed_histories": sum(group["passed_histories"] for group in result["groups"]),
                      "formal_eligible": result["formal_eligible"]}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "structurally_and_execution_validated_candidate" else 1


if __name__ == "__main__":
    raise SystemExit(main())
