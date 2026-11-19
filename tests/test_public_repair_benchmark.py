from dataclasses import replace
from io import BytesIO
import subprocess
from zipfile import ZipFile

import pytest

from evaluation import public_repair_benchmark as benchmark


@pytest.mark.parametrize(("stage", "code", "stdout", "stderr", "expected"), [
    ("baseline", 1, "", benchmark.BUG_MARKER + "\n", "reproduced"),
    ("baseline", 0, benchmark.PASS_MARKER + "\n", "", "not_reproduced"),
    ("baseline", 0, "", "", "probe_or_infrastructure_failure"),
    ("baseline", 1, "", "ImportError: cannot import library", "probe_or_infrastructure_failure"),
    ("baseline", 125, "", benchmark.BUG_MARKER, "probe_or_infrastructure_failure"),
    ("reference", 0, benchmark.PASS_MARKER + "\n", "", "reference_passed"),
    ("reference", 0, "", "", "probe_or_infrastructure_failure"),
    ("reference", 1, "", benchmark.BUG_MARKER, "defect_persists"),
])
def test_probe_requires_explicit_completion_or_defect_evidence(stage, code, stdout, stderr, expected):
    assert benchmark.classify_probe(code, stdout, stderr, stage) == expected


@pytest.mark.parametrize(("code", "stdout", "stderr", "expected"), [
    (0, benchmark.PASS_MARKER + "\n", "", "candidate_passed"),
    (0, "", "", "probe_or_infrastructure_failure"),
    (1, "", benchmark.FAIL_MARKER + "\n", "candidate_failed"),
    (1, "", benchmark.BUG_MARKER + "\n", "candidate_failed"),
    (125, "", benchmark.FAIL_MARKER, "probe_or_infrastructure_failure"),
])
def test_candidate_probe_classification_requires_oracle_marker(code, stdout, stderr, expected):
    assert benchmark.classify_candidate_probe(code, stdout, stderr) == expected


@pytest.mark.parametrize("path", ["tests/test_core.py", "src/config.json"])
def test_candidate_patch_rejects_paths_outside_python_source_tree(path):
    patch = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(benchmark.RepairToolError, match="src/"):
        benchmark.apply_candidate_patch(patch, workspace=None)


def test_candidate_patch_applies_only_after_scope_validation(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / "src" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("answer = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    patch = ("--- a/src/sample.py\n+++ b/src/sample.py\n@@ -1 +1 @@\n"
             "-answer = 1\n+answer = 2\n")

    result = benchmark.apply_candidate_patch(patch, workspace)

    assert result["paths"] == ["src/sample.py"]
    assert source.read_text(encoding="utf-8") == "answer = 2\n"


def test_candidate_is_scored_on_a_copy_only_after_admission(tmp_path, monkeypatch):
    case_id = "attrs-1513"
    baseline = tmp_path / "baseline"
    original = baseline / "src" / "sample.py"
    original.parent.mkdir(parents=True)
    original.write_text("answer = 1\n", encoding="utf-8")
    candidate = tmp_path / "candidate.diff"
    candidate.write_bytes(b"--- a/src/sample.py\r\n+++ b/src/sample.py\r\n@@ -1 +1 @@\r\n-answer = 1\r\n+answer = 2\r\n")
    oracle = tmp_path / "oracle.py"

    def observe(source, mounted_oracle, stage):
        assert mounted_oracle == oracle
        assert stage == "candidate"
        assert (source / "sample.py").read_text(encoding="utf-8") == "answer = 2\n"
        return {"category": "candidate_passed"}

    monkeypatch.setattr(benchmark, "probe", observe)
    assert benchmark.score_candidate(case_id, None, baseline, oracle, tmp_path, True)["category"] == "candidate_not_provided"
    blocked = benchmark.score_candidate(case_id, candidate, baseline, oracle, tmp_path, False)
    assert blocked["reason"] == "public_defect_benchmark_not_admitted"
    assert not (tmp_path / "candidate-work").exists()

    result = benchmark.score_candidate(case_id, candidate, baseline, oracle, tmp_path, True)

    assert result["category"] == "candidate_passed"
    assert result["diff_sha256"] == benchmark.sha256(candidate.read_bytes())
    assert result["workflow_evaluated"] is result["reviewable"] is False
    assert original.read_text(encoding="utf-8") == "answer = 1\n"


def test_snapshot_keeps_only_python_sources_and_license(tmp_path, monkeypatch):
    archive = BytesIO()
    with ZipFile(archive, "w") as contents:
        contents.writestr("sample-revision/LICENSE", "license")
        contents.writestr("sample-revision/src/sample/core.py", "answer = 42\n")
        contents.writestr("sample-revision/tests/test_core.py", "upstream test")
    data = archive.getvalue()
    case = replace(benchmark.CASES["attrs-1513"], repository="example/sample")
    monkeypatch.setattr(benchmark, "fetch", lambda url: data)

    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        benchmark.snapshot(case, ("revision", "wrong"), destination)
    assert not destination.exists()
    benchmark.snapshot(case, ("revision", benchmark.sha256(data)), destination)
    assert (destination / "LICENSE").read_text() == "license"
    assert (destination / "src/sample/core.py").read_text() == "answer = 42\n"
    assert not (destination / "tests").exists()
