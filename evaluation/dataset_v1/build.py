"""Build the dataset-v1 final-patch evaluation set.

Usage (from the project root)::

    .venv\\Scripts\\python.exe -m evaluation.dataset_v1.build
    .venv\\Scripts\\python.exe -m evaluation.dataset_v1.build --check

Every admission claim written into ``data/manifest.json`` comes from actually
executing the case: baseline tests, reference patch, hidden acceptance tests and
mutation probes. Nothing is asserted from code inspection alone. A case whose
contract does not survive execution fails the build instead of being recorded.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow "python build.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from evaluation.dataset_v1.cases import all_cases
    except ModuleNotFoundError as exc:
        if exc.name != "evaluation.dataset_v1.cases":
            raise
        all_cases = None
else:
    try:
        from .cases import all_cases
    except ModuleNotFoundError as exc:
        if exc.name != "evaluation.dataset_v1.cases":
            raise
        all_cases = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
AGENT_INPUT = DATA / "agent-input"
GRADER_ONLY = DATA / "grader-only"
RESULTS = DATA / "results"
DATASET_VERSION = "dataset-v1-20261117"
TEST_TIMEOUT = 180
DEFAULT_DOCKER_IMAGE = "code-repair-sandbox:py311"


def count_tests(source):
    """Count module level ``def test_`` definitions in a test source string."""
    return sum(1 for line in source.splitlines() if line.startswith("def test_"))


# --------------------------------------------------------------------------- #
# hashing helpers
# --------------------------------------------------------------------------- #
def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root):
    """Return a deterministic file hash map and reject links in frozen data."""
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are not allowed in dataset trees: {0}".format(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def _docker_image():
    image = os.environ.get("REPAIR_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise RuntimeError("REPAIR_DOCKER_IMAGE is not a valid image reference")
    return image


def docker_runtime():
    """Require the configured local image and return the frozen runner identity."""
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI is required; host execution is disabled")
    image = _docker_image()
    try:
        engine = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=True)
        image_id = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=True)
        versions = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--pids-limit", "32", "--memory", "128m", "--cpus", "0.5",
             image, "python", "-c",
             "import json,sys,pytest; print(json.dumps({'python':sys.version.split()[0], 'pytest':pytest.__version__}))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError("Docker runner preflight failed: {0}".format(detail.strip()[-800:])) from exc
    return {
        "image": image,
        "image_id": image_id.stdout.strip(),
        "engine_version": engine.stdout.strip(),
        **json.loads(versions.stdout.strip().splitlines()[-1]),
    }


def combined_source_hash(files):
    lines = ["{0}:{1}".format(name, sha256_text(files[name])) for name in sorted(files)]
    return sha256_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# patch helpers
# --------------------------------------------------------------------------- #
def unified_patch(buggy_files, fixed_files):
    """Return one unified diff (``a/`` ``b/`` prefixes) for the changed files."""
    chunks = []
    for name in sorted(fixed_files):
        before = buggy_files[name]
        after = fixed_files[name]
        if before == after:
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    fromfile="a/{0}".format(name),
                    tofile="b/{0}".format(name),
                )
            )
        )
    return "".join(chunks)


def apply_patch(directory, patch_text):
    """Apply ``patch_text`` inside ``directory`` with ``git apply``."""
    handle, patch_path = tempfile.mkstemp(suffix=".diff")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(patch_text)
        proc = subprocess.run(
            ["git", "apply", "-p1", "--whitespace=nowarn", patch_path],
            cwd=str(directory),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT,
        )
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
                "stderr": proc.stderr.strip()[:800]}
    finally:
        os.unlink(patch_path)


# --------------------------------------------------------------------------- #
# test execution helpers
# --------------------------------------------------------------------------- #
def _parse_junit(path):
    if not os.path.exists(path):
        return {"tests": None, "failures": None, "errors": None, "skipped": None}
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return {"tests": None, "failures": None, "errors": None, "skipped": None}
    root = tree.getroot()
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall(".//testsuite")
    if not suites:
        return {"tests": None, "failures": None, "errors": None, "skipped": None}
    attrs = root.attrib if root.tag == "testsuites" and "tests" in root.attrib else None
    if attrs is None:
        return {
            name: sum(int(suite.attrib.get(name, 0)) for suite in suites)
            for name in ("tests", "failures", "errors", "skipped")
        }
    return {
        "tests": int(attrs.get("tests", 0)),
        "failures": int(attrs.get("failures", 0)),
        "errors": int(attrs.get("errors", 0)),
        "skipped": int(attrs.get("skipped", 0)),
    }


def run_tests(directory, test_file):
    """Run one case in a constrained Docker container and return its evidence."""
    junit_dir = tempfile.mkdtemp(prefix="junit-")
    junit = os.path.join(junit_dir, "report.xml")
    name = "dv1-test-{0}".format(uuid.uuid4().hex)
    source = str(Path(directory).resolve()).replace("\\", "/")
    reports = str(Path(junit_dir).resolve()).replace("\\", "/")
    image = _docker_image()
    command = [
        "docker", "run", "--name", name, "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
        "--tmpfs", "/workspace:rw,nosuid,nodev,size=128m,mode=0777",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        "--mount", "type=bind,source={0},target=/input,readonly".format(source),
        "--mount", "type=bind,source={0},target=/reports".format(reports),
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTEST_ADDOPTS=",
        "--env", "PYTHONPATH=/workspace", "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
    ]
    if os.name == "nt":
        command.extend(["--user", "0:0"])
    else:
        command.extend(["--user", "{0}:{1}".format(os.getuid(), os.getgid())])
    command.extend([
        image, "python", "-c",
        "import os,shutil,sys; shutil.copytree('/input','/workspace',dirs_exist_ok=True); "
        "os.chdir('/workspace'); os.execv(sys.executable,[sys.executable,'-m','pytest','-q',"
        "'-p','no:cacheprovider','--tb=short','--junitxml=/reports/report.xml',sys.argv[1]])",
        test_file,
    ])
    record = None
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TEST_TIMEOUT)
        counts = _parse_junit(junit)
        record = {
            "test_file": test_file,
            "exit_code": proc.returncode,
            "command": "docker run --network none --read-only --cap-drop ALL {0}".format(test_file),
            **counts,
            "tail": ((proc.stdout or "") + (proc.stderr or "")).strip()[-600:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        record = {"test_file": test_file, "exit_code": None, "command": None,
                  "tests": None, "failures": None, "errors": None, "skipped": None,
                  "tail": "timeout", "timed_out": True}
    except OSError as exc:
        record = {"test_file": test_file, "exit_code": None, "command": None,
                  "tests": None, "failures": None, "errors": None, "skipped": None,
                  "tail": "docker execution failed: {0}".format(str(exc)[:500]), "timed_out": False}
    finally:
        cleanup_error = None
        try:
            cleanup = subprocess.run(["docker", "rm", "--force", name], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=20)
            if cleanup.returncode != 0:
                cleanup_error = (cleanup.stderr or cleanup.stdout or "docker rm failed").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_error = str(exc)
        shutil.rmtree(junit_dir, ignore_errors=True)
    if cleanup_error:
        record["container_cleanup"] = False
        record["tail"] = "container cleanup failed: {0}".format(cleanup_error[-500:])
    else:
        record["container_cleanup"] = True
    return record


def _clean(record):
    ok = record["tests"] is not None and record["tests"] > 0
    return {
        "tests": record["tests"],
        "failures": record["failures"],
        "errors": record["errors"],
        "skipped": record["skipped"],
        "collected": bool(ok),
        "exit_code": record["exit_code"],
        "container_cleanup": record.get("container_cleanup", False),
    }


def materialize(files, target):
    for name, source in files.items():
        path = Path(target) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")


def stage_case(case, include_acceptance=False):
    """Copy the agent-visible files of ``case`` into a fresh temporary directory."""
    work = Path(tempfile.mkdtemp(prefix="dv1-{0}-".format(case["case_id"])))
    materialize(case["files"], work)
    (work / "test_existing.py").write_text(case["existing_tests"], encoding="utf-8", newline="\n")
    if include_acceptance:
        (work / "test_acceptance.py").write_text(case["acceptance_tests"], encoding="utf-8", newline="\n")
    return work


# --------------------------------------------------------------------------- #
# admission
# --------------------------------------------------------------------------- #
def admit(case):
    """Execute the case contract and return the admission evidence."""
    reference_patch = unified_patch(case["files"], case["fixed_files"])
    failures = []
    expected_existing = count_tests(case["existing_tests"])
    expected_acceptance = count_tests(case["acceptance_tests"])
    if expected_existing < 3 or expected_acceptance < 3:
        failures.append("a case needs at least 3 visible and 3 acceptance tests")

    if not reference_patch.strip():
        return {"ok": False, "failures": ["reference patch is empty"], "evidence": {}}

    baseline = stage_case(case, include_acceptance=True)
    try:
        base_existing = run_tests(baseline, "test_existing.py")
        base_acceptance = run_tests(baseline, "test_acceptance.py")
    finally:
        shutil.rmtree(baseline, ignore_errors=True)

    if not _tests_passed(base_existing, expected_existing):
        failures.append("baseline existing tests are not {0} passing".format(expected_existing))
    if not (base_acceptance["tests"] == expected_acceptance
            and base_acceptance["failures"] is not None and base_acceptance["failures"] >= 1
            and base_acceptance["errors"] == 0
            and base_acceptance["skipped"] == 0 and base_acceptance["exit_code"] != 0
            and base_acceptance.get("container_cleanup") is True):
        failures.append("baseline acceptance tests do not fail as expected")

    fixed = stage_case(case, include_acceptance=True)
    try:
        applied = apply_patch(fixed, reference_patch)
        if not applied["ok"]:
            failures.append("reference patch does not apply: {0}".format(applied["stderr"]))
        fix_existing = run_tests(fixed, "test_existing.py")
        fix_acceptance = run_tests(fixed, "test_acceptance.py")
        patched_source = {name: (fixed / name).read_text(encoding="utf-8")
                          for name in case["fixed_files"]}
        patched_existing_sha = sha256_file(fixed / "test_existing.py")
        patched_acceptance_sha = sha256_file(fixed / "test_acceptance.py")
    finally:
        shutil.rmtree(fixed, ignore_errors=True)

    if not _tests_passed(fix_existing, expected_existing):
        failures.append("reference patch does not keep the existing tests green")
    if not _tests_passed(fix_acceptance, expected_acceptance):
        failures.append("reference patch does not pass the acceptance tests")
    if patched_source != case["fixed_files"]:
        failures.append("applied patch does not reproduce the reference source")
    if patched_existing_sha != sha256_text(case["existing_tests"]):
        failures.append("existing tests changed while applying the reference patch")
    if patched_acceptance_sha != sha256_text(case["acceptance_tests"]):
        failures.append("acceptance tests changed while applying the reference patch")

    mutation_records = []
    for name, old, new in case["mutations"]:
        mutated = {path: source for path, source in case["fixed_files"].items()}
        hits = [path for path, source in mutated.items() if old in source]
        if not hits:
            failures.append("mutation {0} does not match the reference source".format(name))
            mutation_records.append({"name": name, "detected": False, "reason": "pattern missing"})
            continue
        for path in hits:
            mutated[path] = mutated[path].replace(old, new)
        work = Path(tempfile.mkdtemp(prefix="dv1-mut-"))
        try:
            materialize(mutated, work)
            (work / "test_existing.py").write_text(case["existing_tests"], encoding="utf-8", newline="\n")
            (work / "test_acceptance.py").write_text(case["acceptance_tests"], encoding="utf-8", newline="\n")
            record = run_tests(work, "test_acceptance.py")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        detected = (
            record["tests"] == expected_acceptance and record["skipped"] == 0
            and record["exit_code"] != 0
            and record["failures"] is not None and record["failures"] >= 1
            and record["errors"] == 0
            and record.get("container_cleanup") is True
        )
        if not detected:
            failures.append("mutation {0} survives the acceptance tests".format(name))
        mutation_records.append({
            "name": name,
            "detected": detected,
            "acceptance_failures": record["failures"],
            "acceptance_errors": record["errors"],
            "acceptance_skipped": record["skipped"],
            "acceptance_tests": record["tests"],
            "exit_code": record["exit_code"],
            "container_cleanup": record.get("container_cleanup"),
        })

    return {
        "ok": not failures,
        "failures": failures,
        "expected_existing": expected_existing,
        "expected_acceptance": expected_acceptance,
        "evidence": {
            "baseline_existing": _clean(base_existing),
            "baseline_acceptance": _clean(base_acceptance),
            "reference_existing": _clean(fix_existing),
            "reference_acceptance": _clean(fix_acceptance),
            "patch_apply": applied,
            "mutations": mutation_records,
            "baseline_existing_tail": base_existing["tail"][-300:],
        },
        "reference_patch": reference_patch,
    }


def _tests_passed(record, expected):
    return (
        record["tests"] == expected and record["failures"] == 0
        and record["errors"] == 0 and record["skipped"] == 0
        and record["exit_code"] == 0 and not record["timed_out"]
        and record.get("container_cleanup") is True
    )


# --------------------------------------------------------------------------- #
# dataset layout
# --------------------------------------------------------------------------- #
def write_case(case, admission, split, agent_root=None, grader_root=None,
               dataset_version=DATASET_VERSION):
    agent_root = Path(agent_root or AGENT_INPUT)
    grader_root = Path(grader_root or GRADER_ONLY)
    agent_dir = agent_root / case["case_id"]
    grader_dir = grader_root / case["case_id"]
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    if grader_dir.exists():
        shutil.rmtree(grader_dir)
    agent_dir.mkdir(parents=True)
    grader_dir.mkdir(parents=True)

    materialize(case["files"], agent_dir)
    (agent_dir / "test_existing.py").write_text(case["existing_tests"], encoding="utf-8", newline="\n")
    task = {
        "case_id": case["case_id"],
        "dataset_version": dataset_version,
        "title": case["title"],
        "report": case["report"],
        "contract": case["contract"],
        "language": "python",
        "runtime": "python-3.11",
        "allowed_paths": case["allowed_paths"],
        "protected_paths": case["protected_paths"],
        "existing_test_command": "python -m pytest -q test_existing.py",
        "split": split,
    }
    (agent_dir / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")

    (grader_dir / "test_acceptance.py").write_text(
        case["acceptance_tests"], encoding="utf-8", newline="\n")
    (grader_dir / "reference.patch").write_text(
        admission["reference_patch"], encoding="utf-8", newline="\n")
    labels = {
        "case_id": case["case_id"],
        "contract": case["contract"],
        "expected": {
            "baseline_existing": "{0} passed".format(admission["expected_existing"]),
            "baseline_acceptance": "at least 1 failure",
            "reference_existing": "{0} passed".format(admission["expected_existing"]),
            "reference_acceptance": "{0} passed".format(admission["expected_acceptance"]),
        },
        "mutations": [name for name, _, _ in case["mutations"]],
        "split": split,
        "group_id": case["group_id"],
    }
    (grader_dir / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")


def assign_splits(cases):
    """Assign splits per group so no group straddles two splits."""
    groups = sorted({case["group_id"] for case in cases})
    total = len(groups)
    validation_count = max(1, round(total * 0.25))
    test_count = max(1, round(total * 0.25))
    dev_count = total - validation_count - test_count
    mapping = {}
    for index, group in enumerate(groups):
        if index < dev_count:
            mapping[group] = "dev"
        elif index < dev_count + validation_count:
            mapping[group] = "validation"
        else:
            mapping[group] = "test"
    return mapping


def build_coverage(cases):
    families = {}
    difficulties = {}
    files_per_case = {}
    for case in cases:
        families[case["defect_family"]] = families.get(case["defect_family"], 0) + 1
        difficulties[case["difficulty"]] = difficulties.get(case["difficulty"], 0) + 1
        changed = sum(1 for name in case["fixed_files"]
                      if case["fixed_files"][name] != case["files"].get(name))
        files_per_case[case["case_id"]] = {
            "files_total": len(case["files"]),
            "files_changed": changed,
        }
    return {
        "defect_family": dict(sorted(families.items())),
        "difficulty": dict(sorted(difficulties.items())),
        "multi_file_cases": [cid for cid, info in files_per_case.items() if info["files_total"] > 1],
        "single_file_cases": [cid for cid, info in files_per_case.items() if info["files_total"] == 1],
        "gaps": [
            "并发/线程与异步竞态：本批无样本",
            "真实 Issue 来源：本批全部为合成题，无真实仓库缺陷",
            "跨仓库/跨语言：仅 Python 单仓库，跨模块只覆盖包内两级调用",
        ],
    }


def build_dataset(cases, data_dir, dataset_version, generator_label,
                  coverage_gaps=None, extra_blockers=None, check_only=False):
    """Admit every case in staging, then publish a complete frozen snapshot."""
    data_dir = Path(data_dir)

    if shutil.which("git") is None:
        raise SystemExit("git is required to apply reference patches")
    if not cases:
        raise SystemExit("no cases selected")

    try:
        runner = docker_runtime()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    stage_parent = None
    staged_data = None
    if not check_only:
        data_dir.parent.mkdir(parents=True, exist_ok=True)
        stage_parent = Path(tempfile.mkdtemp(prefix=".{0}-build-".format(data_dir.name),
                                             dir=str(data_dir.parent)))
        staged_data = stage_parent / "data"
        (staged_data / "agent-input").mkdir(parents=True)
        (staged_data / "grader-only").mkdir(parents=True)

    splits = assign_splits(cases)
    records = []
    all_ok = True

    try:
        for case in cases:
            admission = admit(case)
            split = splits[case["group_id"]]
            status = "ok" if admission["ok"] else "FAILED"
            print("[{0}] {1} {2}".format(status, case["case_id"], case["title"]))
            for problem in admission["failures"]:
                print("    - {0}".format(problem))
            if not admission["ok"]:
                all_ok = False
            if check_only:
                continue
            if not admission["ok"]:
                continue
            write_case(case, admission, split, staged_data / "agent-input",
                       staged_data / "grader-only", dataset_version)

            evidence = admission["evidence"]
            records.append({
                "case_id": case["case_id"],
                "dataset_version": dataset_version,
                "split": split,
                "group_id": case["group_id"],
                "title": case["title"],
                "defect_family": case["defect_family"],
                "difficulty": case["difficulty"],
                "provenance": {
                    "kind": "synthetic",
                    "generator": generator_label,
                    "method": "hand-written analogue of a common defect, not a real issue",
                    "repository": None,
                    "issue": None,
                    "license": "project-internal",
                    "collected_at": current_timestamp(),
                    "disclosure": "模型生成的合成题；需未参与生成的人复核后才可作为正式真值",
                },
                "language": "python",
                "runtime": "python-3.11",
                "dependencies": ["pytest==9.1.1"],
                "base_revision": "synthetic-{0}".format(case["case_id"]),
                "source_sha256": {
                    "files": {name: sha256_text(source) for name, source in case["files"].items()},
                    "combined": combined_source_hash(case["files"]),
                },
                "report": case["report"],
                "expected_behavior": case["contract"],
                "reproduce_command": {
                    "existing": "python -m pytest -q test_existing.py",
                    "acceptance": "python -m pytest -q test_acceptance.py",
                    "cwd": "agent-input/{0}".format(case["case_id"]),
                    "timeout_seconds": TEST_TIMEOUT,
                    "env": ["PYTHONPATH=/workspace", "PYTHONDONTWRITEBYTECODE=1"],
                    "sandbox": "Docker; network disabled; read-only input mount",
                },
                "allowed_paths": case["allowed_paths"],
                "protected_paths": case["protected_paths"],
                "existing_test_manifest": {
                    "file": "test_existing.py",
                    "sha256": sha256_text(case["existing_tests"]),
                    "expected_tests": admission["expected_existing"],
                    "baseline_result": evidence["baseline_existing"],
                    "reference_result": evidence["reference_existing"],
                },
                "oracle_manifest": {
                    "acceptance_file": "test_acceptance.py",
                    "acceptance_sha256": sha256_text(case["acceptance_tests"]),
                    "expected_tests": admission["expected_acceptance"],
                    "baseline_result": evidence["baseline_acceptance"],
                    "reference_result": evidence["reference_acceptance"],
                    "mutation_probes": evidence["mutations"],
                },
                "reference_patch_hash": sha256_text(admission["reference_patch"]),
                "review": {
                    "annotator": "model-generated (this build)",
                    "independent_reviewer": None,
                    "decision": "pending",
                    "notes": "准入证据由程序执行产生；独立人工复核未完成",
                },
                "admission": {
                    "checked_at": current_timestamp(),
                    "ok": True,
                    "failures": [],
                    "patch_apply_exit_code": evidence["patch_apply"]["exit_code"],
                },
                "artifact_sha256": {
                    "agent_input": tree_sha256(staged_data / "agent-input" / case["case_id"]),
                    "grader_only": tree_sha256(staged_data / "grader-only" / case["case_id"]),
                },
            })

        if check_only:
            if not all_ok:
                raise SystemExit(1)
            print("all selected cases admitted in --check mode; no files changed")
            return {"ok": True, "records": [], "manifest": None, "digest": None}

        if not all_ok:
            raise SystemExit("admission failed; existing frozen data was not changed")

        coverage = build_coverage(cases)
        if coverage_gaps:
            coverage["gaps"] = list(coverage_gaps)
        blockers = extra_blockers or [
            "全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录",
            "independent_reviewer 未签署，review.decision=pending",
            "并发、跨仓库、多语言维度无样本",
            "评分器只覆盖单次 pass@1，未接入重复采样与预算统计",
        ]
        manifest = {
            "schema_version": 2,
            "dataset_version": dataset_version,
            "created_at": current_timestamp(),
            "generator": generator_label,
            "isolation": {
                "agent_visible": "data/agent-input/<case_id>",
                "grader_only": "data/grader-only/<case_id>",
                "rule": "只把单个 agent-input 目录挂载给被测 Agent；grader-only 不得进入 Agent 可读路径、RAG 语料或同一可枚举挂载",
            },
            "runtime": {**runner, "host_platform": platform.platform()},
            "case_count": len(records),
            "splits": split_counts(records),
            "coverage": coverage,
            "formal_eligible": False,
            "formal_blockers": blockers,
            "cases": records,
        }

        manifest_path = staged_data / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        digest = sha256_file(manifest_path)
        (staged_data / "manifest.sha256").write_text(
            "{0}  manifest.json\n".format(digest), encoding="utf-8", newline="\n")
        publish_snapshot(staged_data, data_dir)
        (data_dir / "results").mkdir(parents=True, exist_ok=True)

        print("built {0} cases -> {1}".format(len(records), data_dir))
        print("manifest sha256: {0}".format(digest))
        print("splits: {0}".format(manifest["splits"]))
        return {"ok": True, "records": records, "manifest": manifest, "digest": digest}
    finally:
        if stage_parent is not None:
            shutil.rmtree(stage_parent, ignore_errors=True)


def publish_snapshot(staged_data, data_dir):
    """Replace the frozen trees and digest together, rolling back on errors."""
    staged_data = Path(staged_data)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=".{0}-publish-".format(data_dir.name),
                                   dir=str(data_dir.parent)))
    names = ("agent-input", "grader-only", "manifest.json", "manifest.sha256")
    previous = []
    installed = []
    try:
        for name in names:
            target = data_dir / name
            source = staged_data / name
            saved = backup / name
            if target.exists():
                os.replace(str(target), str(saved))
                previous.append((saved, target))
            os.replace(str(source), str(target))
            installed.append(target)
    except OSError as exc:
        rollback_errors = []
        for target in reversed(installed):
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        for saved, target in reversed(previous):
            if saved.exists():
                try:
                    os.replace(str(saved), str(target))
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError(
                "snapshot publish failed ({0}); rollback incomplete, preserved backup at {1}: {2}"
                .format(exc, backup, "; ".join(rollback_errors))) from exc
        shutil.rmtree(backup, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the dataset-v1 patch evaluation set.")
    parser.add_argument("--check", action="store_true", help="run the admission checks only")
    parser.add_argument("--only", default=None, help="comma separated case ids; requires --check")
    args = parser.parse_args(argv)

    if args.only and not args.check:
        parser.error("--only is a non-publishing check and requires --check")

    if all_cases is None:
        parser.error("private case definitions are not included in this checkout")
    cases = all_cases()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        unknown = wanted - {case["case_id"] for case in cases}
        if unknown:
            parser.error("unknown case id(s): {0}".format(", ".join(sorted(unknown))))
        cases = [case for case in cases if case["case_id"] in wanted]

    build_dataset(cases, DATA, DATASET_VERSION, "evaluation/dataset_v1/cases",
                  check_only=args.check)
    return 0


def current_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def split_counts(records):
    counts = {"dev": 0, "validation": 0, "test": 0}
    for record in records:
        counts[record["split"]] += 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
