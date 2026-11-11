"""Check that evaluation cannot confuse broken validation with a real repair."""
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_benchmark as generator
import run_benchmark as evaluator


def first_record():
    return json.loads((evaluator.ROOT / "benchmark.json").read_text(encoding="utf-8"))["cases"][0]


def test_task_inputs_do_not_contain_oracles_or_retrieval_answers():
    records = json.loads((evaluator.ROOT / "benchmark.json").read_text(encoding="utf-8"))["cases"]
    histories = [json.loads(path.read_text(encoding="utf-8"))
                 for path in (evaluator.ROOT / "history_cases").glob("*.json")]
    labels = json.loads((evaluator.ROOT / "relevance.json").read_text(encoding="utf-8"))["labels"]
    identifiers = {item["experience_id"] for item in histories}
    assert len(records) == len(histories) == len(identifiers) == 30
    assert set(labels) == {item["case_id"] for item in records}
    for record in records:
        task_root = evaluator.dataset_path(record["repository"])
        assert {path.name for path in task_root.iterdir() if path.is_file()} == {
            "task.json", "target.py", "test_existing.py"}
        assert set(labels[record["case_id"]]) <= identifiers
        assert not task_root.joinpath("gold").exists()
        task = json.loads((task_root / "task.json").read_text(encoding="utf-8"))
        assert "patch" not in task and "relevant_case_ids" not in task
        assert all(record["query"] != item["original_report"] for item in histories)
    assert all("review_decision" not in item and "validation" not in item for item in histories)


def test_partial_patch_passing_existing_tests_does_not_count_as_repair(tmp_path):
    record = first_record()
    patches = tmp_path / "patches"
    patches.mkdir()
    # Multiplying b still passes every old check where b==0, but fails the defect.
    patch = generator.patch_for("def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a - 2 * b\n")
    evaluator.write_text(patches / "case-01.patch", patch)
    report = evaluator.evaluate_patches([record], patches, tmp_path / "execution")
    assert report["repair_successes"] == 0
    assert report["existing_tests_passed"] == 3
    assert report["cases"][0]["tests"]["groups"]["test_acceptance"]["failed"] == 3


def test_reference_patch_is_checked_by_execution_and_missing_patch_stays_unsuccessful(tmp_path):
    record = first_record()
    missing = {**record, "case_id": "case-missing"}
    patches = tmp_path / "patches"
    patches.mkdir()
    reference = evaluator.dataset_path(record["oracle"]) / "reference.patch"
    evaluator.write_text(patches / "case-01.patch", reference.read_text(encoding="utf-8"))
    report = evaluator.evaluate_patches([record, missing], patches, tmp_path / "execution")
    assert report["repair_successes"] == 1
    assert report["total_tasks"] == 2
    assert report["existing_tests_expected"] == 6
    assert report["existing_tests_passed"] == 3
    assert report["regression_pass_rate"] == 0.5
    assert report["cases"][1]["reason"] == "missing_patch"


def test_patch_cannot_change_test_files(tmp_path):
    evaluator.write_text(tmp_path / "test_existing.py", "assert False\n")
    patch = generator.patch_for("assert False\n", "assert True\n", filename="test_existing.py")
    result = evaluator.apply_candidate(tmp_path, patch)
    assert not result["applied"]
    assert (tmp_path / "test_existing.py").read_text() == "assert False\n"


def test_collection_failure_is_not_a_reproduced_defect(tmp_path):
    evaluator.write_text(tmp_path / "test_bad.py", "from missing_benchmark_module import value\n")
    report = evaluator.run_pytest(tmp_path, ["test_bad.py"])
    assert not report["infrastructure_ok"]


def test_keyword_retrieval_uses_production_search_without_external_embeddings(tmp_path):
    document = json.loads((evaluator.ROOT / "history_cases" / "experience-01.json").read_text(encoding="utf-8"))
    checked = evaluator.check_experience(document, tmp_path / "checked")
    assert checked["valid"]
    record = first_record()
    report = evaluator.evaluate_retrieval([record], [document],
        {record["case_id"]: [document["experience_id"]]}, tmp_path, "keyword", 0.35)
    assert report["hits"] == 1
    assert report["details"][0]["returned_modes"] == ["keyword"]


def test_no_agent_submissions_does_not_make_up_successes(tmp_path):
    report = evaluator.evaluate_agent([first_record()], None, None, tmp_path)
    assert report["measured"] is False
    assert "baseline" not in report and "rag" not in report


def test_generator_refuses_cleanup_outside_evaluation(tmp_path):
    outside = tmp_path / "preserve"
    outside.mkdir()
    with pytest.raises(ValueError, match="拒绝清理"):
        generator.reset_generated_directory(outside)
    assert outside.exists()
