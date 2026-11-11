"""Check that evaluation cannot confuse broken validation with a real repair."""
from pathlib import Path
import json
import hashlib
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


def test_windows_checkout_line_endings_do_not_break_reference_patch(tmp_path, monkeypatch):
    record = first_record()
    for relative, names in ((record["repository"], ("target.py", "test_existing.py")),
                            (record["oracle"], ("test_acceptance.py", "reference.patch"))):
        destination = tmp_path / relative
        destination.mkdir(parents=True)
        for name in names:
            text = (evaluator.ROOT / relative / name).read_text(encoding="utf-8")
            (destination / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    copied = tmp_path / "execution"
    evaluator.copy_task(record, copied)
    patch = (tmp_path / record["oracle"] / "reference.patch").read_text(encoding="utf-8")
    assert evaluator.apply_candidate(copied, patch)["applied"]


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


def test_ranking_metrics_use_relevant_count_and_fixed_k_denominator():
    metrics = evaluator.ranking_metrics(["irrelevant", "target", "another"], ["target", "also-relevant"])
    assert metrics["recall@3"] == 0.5
    assert metrics["precision@3"] == 1 / 3
    assert metrics["mrr"] == 0.5
    assert metrics["mrr@1"] == 0.0


def test_outcome_and_range_filter_evaluation_checks_success_failure_and_old_metadata(tmp_path):
    report = evaluator.evaluate_outcome_and_scope_filters(tmp_path)
    assert report == {
        "passed": True,
        "accepted_ids": ["accepted-match"],
        "rejected_ids": ["rejected-match"],
        "excluded_missing_metadata": True,
    }


def test_experiment_manifest_requires_identical_model_prompt_and_budget(tmp_path):
    benchmark_hash = evaluator.digest((evaluator.ROOT / "benchmark.json").read_text(encoding="utf-8"))
    budget = {"runtime_seconds": 120, "tokens": 20000, "cost_yuan": 0.1, "repair_attempts": 10}
    common = {"model": "fixed-model", "prompt_sha256": "a" * 64, "budget": budget,
              "patch_dir": "patches", "trace_dir": "traces"}
    manifest = {
        "experiment_id": "paired-001", "model": "fixed-model", "prompt_sha256": "a" * 64,
        "dataset_sha256": benchmark_hash, "budget": budget,
        "runs": {"baseline": dict(common), "rag": dict(common)},
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded = evaluator.load_experiment_manifest(path, benchmark_hash)
    assert loaded["runs"]["baseline"]["patch_dir"] == (tmp_path / "patches").resolve()

    manifest["runs"]["rag"]["budget"] = {**budget, "tokens": 10000}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="相同预算"):
        evaluator.load_experiment_manifest(path, benchmark_hash)


def test_experiment_artifact_validation_requires_patch_and_nonempty_valid_trace(tmp_path):
    records = [{"case_id": "case-01"}]
    patch_dir, trace_dir = tmp_path / "patches", tmp_path / "traces"
    patch_dir.mkdir()
    trace_dir.mkdir()
    (patch_dir / "case-01.patch").write_text("patch", encoding="utf-8")
    (trace_dir / "case-01.jsonl").write_text('{"event":"done"}\n', encoding="utf-8")
    run = {"patch_dir": patch_dir, "trace_dir": trace_dir}
    complete = evaluator.validate_experiment_artifacts(records, run)
    assert complete["complete"] is True
    assert complete["artifacts"][0]["patch_sha256"] == hashlib.sha256(b"patch").hexdigest()
    (trace_dir / "case-01.jsonl").write_text("not json\n", encoding="utf-8")
    assert evaluator.validate_experiment_artifacts(records, run)["complete"] is False


def test_generator_refuses_cleanup_outside_evaluation(tmp_path):
    outside = tmp_path / "preserve"
    outside.mkdir()
    with pytest.raises(ValueError, match="拒绝清理"):
        generator.reset_generated_directory(outside)
    assert outside.exists()
