"""Tests for the dataset-v3 build artefacts and the shared scorer.

    .venv\\Scripts\\python.exe -m pytest evaluation/dataset_v3/test_dataset_v3.py -q
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest

from evaluation.dataset_v1.build import sha256_file, sha256_text
from evaluation.dataset_v3 import score as scorer

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST_PATH = DATA / "manifest.json"
AGENT_INPUT = DATA / "agent-input"
GRADER_ONLY = DATA / "grader-only"

pytestmark = pytest.mark.skipif(
    not MANIFEST_PATH.exists() or not GRADER_ONLY.is_dir(),
    reason="dataset manifest or local-only grading materials unavailable")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _case_ids():
    if not MANIFEST_PATH.exists():
        return []
    return [item["case_id"] for item in
            json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["cases"]]


def _patch(case_id, filename, new_source):
    baseline = (AGENT_INPUT / case_id / filename).read_text(encoding="utf-8")
    return "".join(difflib.unified_diff(
        baseline.splitlines(True), new_source.splitlines(True),
        fromfile="a/{0}".format(filename), tofile="b/{0}".format(filename)))


def test_manifest_is_frozen(manifest):
    recorded = (DATA / "manifest.sha256").read_text(encoding="utf-8").split()[0]
    assert recorded == sha256_file(MANIFEST_PATH)
    assert manifest["dataset_version"].startswith("dataset-v3")
    assert manifest["formal_eligible"] is False


def test_registered_cases_match_the_directories(manifest):
    registered = {item["case_id"] for item in manifest["cases"]}
    assert registered == {path.name for path in AGENT_INPUT.iterdir() if path.is_dir()}
    assert manifest["case_count"] == len(registered) == 16


def test_the_agent_tree_holds_no_grading_material(manifest):
    forbidden = {"test_acceptance.py", "reference.patch", "labels.json"}
    for case_id in (item["case_id"] for item in manifest["cases"]):
        names = {path.name for path in (AGENT_INPUT / case_id).rglob("*")}
        assert not (names & forbidden), case_id


def test_each_group_lives_in_a_single_split(manifest):
    seen = {}
    for item in manifest["cases"]:
        seen.setdefault(item["group_id"], set()).add(item["split"])
    assert {group: splits for group, splits in seen.items() if len(splits) > 1} == {}


def test_baseline_hashes_match_disk(manifest):
    for item in manifest["cases"]:
        case_dir = AGENT_INPUT / item["case_id"]
        for name, digest in item["source_sha256"]["files"].items():
            assert sha256_text((case_dir / name).read_text(encoding="utf-8")) == digest


def test_admission_evidence_is_consistent(manifest):
    for item in manifest["cases"]:
        assert item["existing_test_manifest"]["baseline_result"]["failures"] == 0, item["case_id"]
        assert item["existing_test_manifest"]["reference_result"]["failures"] == 0, item["case_id"]
        assert item["oracle_manifest"]["baseline_result"]["failures"] >= 1, item["case_id"]
        assert item["oracle_manifest"]["reference_result"]["failures"] == 0, item["case_id"]
        probes = item["oracle_manifest"]["mutation_probes"]
        assert len(probes) >= 2 and all(probe["detected"] for probe in probes), item["case_id"]


@pytest.mark.parametrize("case_id", _case_ids())
def test_every_reference_patch_scores_a_pass(manifest, case_id):
    patch = (GRADER_ONLY / case_id / "reference.patch").read_text(encoding="utf-8")
    result = scorer.score_case(case_id, patch, "test-ref", manifest)
    assert result["passed"] is True, (case_id, result["category"], result["gates"])


def test_a_comment_only_patch_is_wrong_behavior(manifest):
    record = manifest["cases"][0]
    target = record["allowed_paths"][0]
    source = (AGENT_INPUT / record["case_id"] / target).read_text(encoding="utf-8")
    result = scorer.score_case(record["case_id"], _patch(record["case_id"], target, source + "\n# x\n"),
                               "test-wrong", manifest)
    assert result["category"] in {"wrong_behavior", "incomplete_fix"}


def test_editing_the_visible_tests_is_tampering(manifest):
    case_id = manifest["cases"][0]["case_id"]
    original = (AGENT_INPUT / case_id / "test_existing.py").read_text(encoding="utf-8")
    tampered = original.replace("import pytest", "import pytest  # tweak")
    result = scorer.score_case(case_id, _patch(case_id, "test_existing.py", tampered),
                               "test-tamper", manifest)
    assert result["category"] == "test_tampering"


def test_unparsable_and_empty_patches_are_classified(manifest):
    case_id = manifest["cases"][0]["case_id"]
    assert scorer.score_case(case_id, "nope\n", "t1", manifest)["category"] == "patch_format"
    assert scorer.score_case(case_id, "\n", "t2", manifest)["category"] == "no_candidate"


def test_summary_keeps_the_registered_denominator(manifest, tmp_path):
    case_id = manifest["cases"][0]["case_id"]
    patch = (GRADER_ONLY / case_id / "reference.patch").read_text(encoding="utf-8")
    passing = scorer.score_case(case_id, patch, "test-summary", manifest)
    (tmp_path / "one.json").write_text(json.dumps(passing, ensure_ascii=False), encoding="utf-8")
    summary = scorer.summarize(tmp_path, manifest)
    assert summary["registered_cases"] == 16
    assert summary["missing_cases"] == 15
    assert summary["pass_at_1"] == round(1 / 16, 4)
