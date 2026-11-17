"""Tests for the dataset-v1 build artefacts and the pass@1 scorer.

Run from the project root::

    .venv\\Scripts\\python.exe -m pytest evaluation/dataset_v1/test_dataset_v1.py -q
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pytest

from evaluation.dataset_v1 import score as scorer
from evaluation.dataset_v1 import build as builder
from evaluation.dataset_v1.build import sha256_file, sha256_text, tree_sha256

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST_PATH = DATA / "manifest.json"
AGENT_INPUT = DATA / "agent-input"
GRADER_ONLY = DATA / "grader-only"

pytestmark = pytest.mark.skipif(
    not MANIFEST_PATH.exists() or not GRADER_ONLY.exists(),
    reason="local frozen grader data is unavailable")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _case(manifest, case_id="dv1-01"):
    return next(item for item in manifest["cases"] if item["case_id"] == case_id)


def _patch(case_id, filename, new_source, old_source=None):
    baseline = old_source if old_source is not None else (
        AGENT_INPUT / case_id / filename).read_text(encoding="utf-8")
    return "".join(difflib.unified_diff(
        baseline.splitlines(True), new_source.splitlines(True),
        fromfile="a/{0}".format(filename), tofile="b/{0}".format(filename),
    ))


# --------------------------------------------------------------------------- #
# build artefacts
# --------------------------------------------------------------------------- #
def test_manifest_hash_is_recorded(manifest):
    recorded = (DATA / "manifest.sha256").read_text(encoding="utf-8").split()[0]
    assert recorded == sha256_file(MANIFEST_PATH)
    assert manifest["dataset_version"]
    assert manifest["formal_eligible"] is False
    assert manifest["formal_blockers"]


def test_every_registered_case_exists_on_disk(manifest):
    registered = {item["case_id"] for item in manifest["cases"]}
    on_disk = {path.name for path in AGENT_INPUT.iterdir() if path.is_dir()}
    assert registered == on_disk
    assert manifest["case_count"] == len(registered)


def test_grader_material_is_absent_from_the_agent_tree(manifest):
    forbidden = {"test_acceptance.py", "reference.patch", "labels.json"}
    for case_id in (item["case_id"] for item in manifest["cases"]):
        names = {path.name for path in (AGENT_INPUT / case_id).rglob("*")}
        assert not (names & forbidden), case_id
        grader_names = {path.name for path in (GRADER_ONLY / case_id).rglob("*")}
        assert {"test_acceptance.py", "reference.patch", "labels.json"} <= grader_names


def test_groups_never_span_two_splits(manifest):
    seen = {}
    for item in manifest["cases"]:
        seen.setdefault(item["group_id"], set()).add(item["split"])
    straddling = {group: splits for group, splits in seen.items() if len(splits) > 1}
    assert straddling == {}


def test_baseline_source_matches_the_recorded_hashes(manifest):
    for item in manifest["cases"]:
        case_dir = AGENT_INPUT / item["case_id"]
        recorded = item["source_sha256"]["files"]
        on_disk = {name: sha256_text((case_dir / name).read_text(encoding="utf-8")) for name in recorded}
        assert on_disk == recorded, item["case_id"]
        assert sha256_text((case_dir / "test_existing.py").read_text(encoding="utf-8")) == (
            item["existing_test_manifest"]["sha256"])


def test_all_case_files_are_bound_to_the_manifest(manifest):
    for item in manifest["cases"]:
        frozen = item["artifact_sha256"]
        assert tree_sha256(AGENT_INPUT / item["case_id"]) == frozen["agent_input"]
        assert tree_sha256(GRADER_ONLY / item["case_id"]) == frozen["grader_only"]


def test_admission_evidence_is_consistent(manifest):
    for item in manifest["cases"]:
        existing = item["existing_test_manifest"]["baseline_result"]
        baseline = item["oracle_manifest"]["baseline_result"]
        reference = item["oracle_manifest"]["reference_result"]
        visible_count = item["existing_test_manifest"]["expected_tests"]
        hidden_count = item["oracle_manifest"]["expected_tests"]
        assert existing["tests"] == visible_count and existing["failures"] == 0, item["case_id"]
        assert baseline["tests"] == hidden_count and baseline["failures"] >= 1, item["case_id"]
        assert reference["tests"] == hidden_count and reference["failures"] == 0, item["case_id"]
        assert item["admission"]["ok"] is True


def test_every_mutation_probe_is_detected(manifest):
    for item in manifest["cases"]:
        probes = item["oracle_manifest"]["mutation_probes"]
        assert probes, item["case_id"]
        for probe in probes:
            assert probe["detected"] is True, (item["case_id"], probe["name"])


def test_recorded_test_counts_match_the_files(manifest):
    for item in manifest["cases"]:
        case_dir = AGENT_INPUT / item["case_id"]
        visible = (case_dir / "test_existing.py").read_text(encoding="utf-8")
        hidden = (GRADER_ONLY / item["case_id"] / "test_acceptance.py").read_text(encoding="utf-8")

        def count(source):
            return sum(1 for line in source.splitlines() if line.startswith("def test_"))

        assert count(visible) >= 3, item["case_id"]
        assert count(hidden) >= 3, item["case_id"]
        assert count(visible) == item["existing_test_manifest"]["expected_tests"], item["case_id"]
        assert count(hidden) == item["oracle_manifest"]["expected_tests"], item["case_id"]


# --------------------------------------------------------------------------- #
# scorer hard gates
# --------------------------------------------------------------------------- #
def _case_ids():
    if not MANIFEST_PATH.exists():
        return []
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return [item["case_id"] for item in payload["cases"]]


@pytest.mark.parametrize("case_id", _case_ids())
def test_every_reference_patch_scores_a_pass(manifest, case_id):
    patch = (GRADER_ONLY / case_id / "reference.patch").read_text(encoding="utf-8")
    result = scorer.score_case(case_id, patch, "test-ref", manifest)
    assert result["passed"] is True, (case_id, result["category"], result["gates"])
    assert all(gate["ok"] for gate in result["gates"])


def test_a_wrong_but_applicable_patch_is_wrong_behavior(manifest):
    record = _case(manifest)
    baseline = (AGENT_INPUT / record["case_id"] / "pagination.py").read_text(encoding="utf-8")
    wrong = baseline.replace("size - 1]", "size - 2]")
    result = scorer.score_case(record["case_id"], _patch(record["case_id"], "pagination.py", wrong),
                               "test-wrong", manifest)
    assert result["passed"] is False
    assert result["category"] in {"wrong_behavior", "incomplete_fix"}


def test_editing_the_visible_tests_is_reported_as_tampering(manifest):
    record = _case(manifest)
    original = (AGENT_INPUT / record["case_id"] / "test_existing.py").read_text(encoding="utf-8")
    tampered = original.replace("assert page_slice([], 1, 10) == []", "assert True")
    result = scorer.score_case(record["case_id"], _patch(record["case_id"], "test_existing.py", tampered),
                               "test-tamper", manifest)
    assert result["category"] == "test_tampering"
    assert result["passed"] is False


def test_an_unlisted_file_is_a_scope_violation(manifest):
    record = _case(manifest)
    patch = "".join(difflib.unified_diff(
        [], ["print('sneaky')\n"], fromfile="/dev/null", tofile="b/extra.py"))
    result = scorer.score_case(record["case_id"], patch, "test-scope", manifest)
    assert result["category"] == "scope_violation"


def test_an_unparsable_patch_is_patch_format(manifest):
    record = _case(manifest)
    result = scorer.score_case(record["case_id"], "I fixed the bug in pagination.py\n",
                               "test-format", manifest)
    assert result["category"] == "patch_format"


def test_an_empty_patch_is_no_candidate(manifest):
    record = _case(manifest)
    result = scorer.score_case(record["case_id"], "   \n", "test-empty", manifest)
    assert result["category"] == "no_candidate"


def test_skipped_test_cannot_pass_a_candidate(manifest, monkeypatch):
    def skipped_result(test_file):
        return {
            "test_file": test_file, "exit_code": 0, "tests": 3, "failures": 0,
            "errors": 0, "skipped": 1, "timed_out": False, "container_cleanup": True,
            "tail": "1 skipped",
        }

    monkeypatch.setattr(scorer, "run_tests", lambda directory, test_file: skipped_result(test_file))
    record = _case(manifest)
    patch = (GRADER_ONLY / record["case_id"] / "reference.patch").read_text(encoding="utf-8")
    result = scorer.score_case(record["case_id"], patch, "test-skipped", manifest)
    assert result["passed"] is False
    assert result["category"] == "infrastructure"
    assert not next(gate for gate in result["gates"] if gate["name"] == "tests_complete")["ok"]


def test_case_tree_tampering_is_not_scored(manifest, tmp_path):
    copied_agent = tmp_path / "agent-input"
    shutil.copytree(AGENT_INPUT, copied_agent)
    task_file = copied_agent / "dv1-01" / "task.json"
    task = json.loads(task_file.read_text(encoding="utf-8"))
    task["report"] = "tampered task"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    patch = (GRADER_ONLY / "dv1-01" / "reference.patch").read_text(encoding="utf-8")
    result = scorer.score_case("dv1-01", patch, "test-tampered-tree", manifest,
                               agent_root=copied_agent)
    assert result["category"] == "oracle_invalid"
    assert result["passed"] is False


def test_manifest_checksum_mismatch_is_rejected(tmp_path):
    copied_manifest = tmp_path / "manifest.json"
    copied_manifest.write_text(MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "manifest.sha256").write_text("0" * 64 + "  manifest.json\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA-256 mismatch"):
        scorer.load_manifest(copied_manifest)


def _passing_result(manifest, case_id, run_id="test-summary"):
    record = next(item for item in manifest["cases"] if item["case_id"] == case_id)
    expected_existing = record["existing_test_manifest"]["expected_tests"]
    expected_acceptance = record["oracle_manifest"]["expected_tests"]
    return {
        "run_id": run_id,
        "case_id": case_id,
        "dataset_version": manifest["dataset_version"],
        "split": record["split"],
        "manifest_sha256": sha256_file(MANIFEST_PATH),
        "patch_sha256": "0" * 64,
        "category": None,
        "passed": True,
        "gates": [{"name": name, "ok": True} for name in (
            "frozen_case_intact", "candidate_present", "patch_parseable", "scope_allowed",
            "tests_untouched", "patch_applies", "existing_tests_unchanged", "containers_cleaned",
            "tests_complete", "test_runner_consistent", "no_regression", "defect_fixed",
        )],
        "tests": {
            "existing": {"tests": expected_existing, "failures": 0, "errors": 0,
                         "skipped": 0, "exit_code": 0, "timed_out": False,
                         "container_cleanup": True},
            "acceptance": {"tests": expected_acceptance, "failures": 0, "errors": 0,
                           "skipped": 0, "exit_code": 0, "timed_out": False,
                           "container_cleanup": True},
        },
    }


def test_summary_keeps_the_registered_denominator(manifest, tmp_path):
    record = _case(manifest)
    passing = _passing_result(manifest, record["case_id"])
    (tmp_path / "one.json").write_text(json.dumps(passing, ensure_ascii=False), encoding="utf-8")
    summary = scorer.summarize(tmp_path, manifest)
    assert summary["registered_cases"] == manifest["case_count"]
    assert summary["scored_cases"] == 1
    assert summary["pass_at_1"] == round(1 / manifest["case_count"], 4)
    assert summary["missing_cases"] == manifest["case_count"] - 1
    metrics = {item["metric"]: item for item in summary["metrics"]}
    assert metrics["pass@1"]["denominator"] == manifest["case_count"]
    assert metrics["patch_parse_rate"]["denominator"] == 1
    assert metrics["patch_apply_rate"]["denominator"] == 1
    assert metrics["regression_pass_rate"]["denominator"] == 1
    assert metrics["defect_fix_rate"]["denominator"] == 1
    assert metrics["scope_compliance_rate"]["denominator"] == 1


def test_summary_rejects_duplicate_candidates_for_a_case(manifest, tmp_path):
    result = _passing_result(manifest, "dv1-01")
    (tmp_path / "one.json").write_text(json.dumps(result), encoding="utf-8")
    result["patch_sha256"] = "different candidate"
    (tmp_path / "two.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(SystemExit, match="multiple candidates"):
        scorer.summarize(tmp_path, manifest)


def test_summary_rejects_mixed_run_ids(manifest, tmp_path):
    first = _passing_result(manifest, "dv1-01", "run-a")
    second = _passing_result(manifest, "dv1-02", "run-b")
    (tmp_path / "one.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(SystemExit, match="mix multiple run_id"):
        scorer.summarize(tmp_path, manifest)


def test_summary_rejects_a_pass_that_contains_skipped_tests(manifest, tmp_path):
    result = _passing_result(manifest, "dv1-01")
    result["tests"]["acceptance"]["skipped"] = 1
    (tmp_path / "one.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(SystemExit, match="without complete, unskipped tests"):
        scorer.summarize(tmp_path, manifest)


def test_only_build_option_cannot_publish_a_partial_dataset():
    with pytest.raises(SystemExit) as exc_info:
        builder.main(["--only", "dv1-01"])
    assert exc_info.value.code == 2


def test_failed_build_preserves_the_existing_frozen_dataset(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    sentinel = data_dir / "agent-input" / "old-case" / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep", encoding="utf-8")
    manifest = data_dir / "manifest.json"
    manifest.write_text("old manifest", encoding="utf-8")
    monkeypatch.setattr(builder, "docker_runtime", lambda: {
        "image": "test", "image_id": "sha256:test", "engine_version": "test",
        "python": "3.11", "pytest": "9",
    })
    monkeypatch.setattr(builder, "admit", lambda case: {
        "ok": False, "failures": ["rejected fixture"], "evidence": {}, "reference_patch": "",
    })
    case = {"case_id": "dv1-test", "group_id": "group", "title": "test"}
    with pytest.raises(SystemExit, match="existing frozen data was not changed"):
        builder.build_dataset([case], data_dir, "test", "test")
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert manifest.read_text(encoding="utf-8") == "old manifest"
    assert not list(tmp_path.glob(".data-build-*"))
