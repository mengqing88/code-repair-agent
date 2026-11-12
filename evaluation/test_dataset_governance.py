"""Meaningful leakage, artifact integrity and approval-boundary regression tests."""
import json

import pytest

from evaluation.dataset_governance import (
    canonical, export_task, leakage_groups, release_inventory, review_readiness, sha, verified_history, verify_release,
)


def test_renamed_cross_source_code_and_history_stay_in_one_split():
    records = [
        {"id": "a::case-1", "before": "def add(a,b):\n return a-b\n"},
        {"id": "b::case-2", "before": "def sum_values(x,y):\n return x-y\n"},
        {"id": "a::experience-1", "before": "def unrelated(x):\n return list(reversed(x))\n"},
    ]
    assignments, edges = leakage_groups(records, [("a::case-1", "a::experience-1")])
    assert len({value["family_id"] for value in assignments.values()}) == 1
    assert len({value["split"] for value in assignments.values()}) == 1
    assert any(edge["reason"] == "normalized_ast_candidate" for edge in edges)
    assert leakage_groups(list(reversed(records)), [("a::case-1", "a::experience-1")])[0] == assignments


def make_release(path):
    private = path / "private/a/case-1/input"
    private.mkdir(parents=True)
    for name in ["target.py", "test_existing.py", "task.json"]:
        (private / name).write_text("{}" if name.endswith("json") else "pass\n", encoding="utf-8")
    history = path / "private/a/histories"
    history.mkdir(parents=True)
    for key in ["experience-1", "experience-2"]:
        (history / (key + ".json")).write_text("{}", encoding="utf-8")
    assignments = {"a::case-1": {"family_id": "one", "split": "test"},
                   "a::experience-1": {"family_id": "one", "split": "test"},
                   "a::experience-2": {"family_id": "two", "split": "development"}}
    (path / "assignments.json").write_text(json.dumps(assignments), encoding="utf-8")
    (path / "review-template.json").write_text(json.dumps({"records": [
        {"record_id": key, "producer": "a"} for key in assignments]}), encoding="utf-8")
    files = release_inventory(path)
    release = {"files": files, "content_sha256": sha(canonical(files)), "formal_eligible": False}
    (path / "release.json").write_text(json.dumps(release), encoding="utf-8")
    return release


@pytest.mark.parametrize("change", ["alter", "add", "delete"])
def test_verify_rejects_changed_added_or_missing_frozen_files(tmp_path, change):
    make_release(tmp_path)
    if change == "alter":
        (tmp_path / "assignments.json").write_text("{}")
    elif change == "add":
        (tmp_path / "unexpected.patch").write_text("answer")
    else:
        (tmp_path / "assignments.json").unlink()
    with pytest.raises(ValueError, match="changed"):
        verify_release(tmp_path)


def test_export_cannot_leak_test_family_history_or_oracle(tmp_path):
    release = tmp_path / "release"
    make_release(release)
    result = export_task(release, "a::case-1", tmp_path / "export")
    assert result["corpus_documents"] == 1
    assert {file.name for file in (tmp_path / "export/input").iterdir()} == {"target.py", "test_existing.py", "task.json"}
    assert not list((tmp_path / "export").rglob("*oracle*"))
    with pytest.raises(FileExistsError):
        export_task(release, "a::case-1", tmp_path / "export")


def test_reviews_bind_to_release_and_cannot_self_approve_or_claim_production_validity(tmp_path):
    release = make_release(tmp_path)
    ledger = {"content_sha256": release["content_sha256"], "records": [
        {"record_id": key, "reviewer": "independent-reviewer", "decision": "approve",
         **{name: "review evidence" for name in ["contract_and_tests", "family_annotation", "license_evidence", "relevance_review"]}}
        for key in ["a::case-1", "a::experience-1", "a::experience-2"]]}
    assert review_readiness(tmp_path, ledger)["attestation_complete"]
    assert not review_readiness(tmp_path, ledger)["formal_eligible"]
    ledger["records"][0]["reviewer"] = "a"
    assert not review_readiness(tmp_path, ledger)["attestation_complete"]
    ledger["content_sha256"] = "wrong"
    with pytest.raises(ValueError, match="different"):
        review_readiness(tmp_path, ledger)


def test_duplicate_and_unknown_relevance_ids_are_rejected():
    records = [{"id": "a", "before": "pass"}]
    with pytest.raises(ValueError, match="Duplicate"):
        leakage_groups(records * 2)
    with pytest.raises(ValueError, match="missing"):
        leakage_groups(records, [("a", "missing")])


def test_normalized_history_cannot_sneak_in_changed_report_or_patch():
    raw = {"experience_id": "experience-1", "original_report": "original",
           "patch_diff": "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-a\n+b\n"}
    expected = {**raw, "dataset_id": "a", "dataset_experience_id": "a::experience-1", "normalization_notes": []}
    assert verified_history(raw, expected, "a") == expected
    for key in ["original_report", "patch_diff"]:
        with pytest.raises(ValueError, match="differs"):
            verified_history(raw, {**expected, key: "changed"}, "a")


def test_candidate_cannot_self_promote_by_editing_release_metadata(tmp_path):
    release = make_release(tmp_path)
    (tmp_path / "release.json").write_text(json.dumps({**release, "formal_eligible": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="self-promote"):
        verify_release(tmp_path)
