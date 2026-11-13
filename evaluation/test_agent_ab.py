"""Protect attribution and failure handling in the real Agent experiment."""
import json

import pytest

from evaluation import run_agent_ab as ab
from evaluation.agent_ab_analysis import classify


@pytest.mark.parametrize("tokens", [True, 0, -1, 1.2, ab.BUDGET["tokens"] + 1])
def test_invalid_task_budget_is_rejected_before_any_model_call(tmp_path, tokens, monkeypatch):
    monkeypatch.setattr(ab, "embed_texts", lambda *args, **kwargs: pytest.fail("Invalid budget must not call API"))
    with pytest.raises(ValueError, match="task_tokens"):
        ab.prepare(tmp_path / "not-created", ["case-03"], task_tokens=tokens)
    assert not (tmp_path / "not-created").exists()


def test_unknown_evaluation_usage_stops_retry_and_keeps_incomplete_accounting():
    from resilience import Fault, call_with_retry, execution_scope
    calls = []
    def unavailable(timeout):
        calls.append(timeout)
        raise TimeoutError("simulated response loss")
    with execution_scope(10, max_tokens=45000, on_request=ab.stop_unknown_usage) as context:
        with pytest.raises(Fault, match="用量未知") as error:
            call_with_retry("model.test", unavailable, replay_safe=True,
                classify=lambda exc: Fault("upstream_timeout", "response lost", category="transient", retryable=True))
        assert error.value.code == "evaluation_usage_unknown"
        assert context.usage_known is False
        assert context.cost_known is False
    assert len(calls) == 1


def test_timeout_usage_is_not_reported_as_complete():
    result = {"model_tokens": 41806, "state": {"accounting": {"usage_complete": False}}}
    assert not ab.token_usage_complete(result)
    result["state"]["accounting"]["usage_complete"] = True
    assert ab.token_usage_complete(result)
    result["usage_complete"] = False  # 最终请求超时，覆盖旧的状态快照。
    assert not ab.token_usage_complete(result)
    result["usage_complete"] = True
    result["model_tokens"] = None
    assert not ab.token_usage_complete(result)
    assert not ab.token_usage_complete({"model_tokens": 1, "state": {}})


def test_case_subset_keeps_original_inputs_and_order_and_rejects_invalid_ids():
    full = json.loads((ab.bench.ROOT / "benchmark.json").read_text(encoding="utf-8"))
    selected = ab.select_benchmark(full, ["case-30", "case-03", "case-11", "case-12", "case-19"])
    assert [r["case_id"] for r in selected["cases"]] == ["case-03", "case-11", "case-12", "case-19", "case-30"]
    assert len(full["cases"]) == 30
    assert all(record in full["cases"] for record in selected["cases"])
    for ids in ([], ["case-11", "case-11"], ["missing"]):
        with pytest.raises(ValueError, match="case_ids"):
            ab.select_benchmark(full, ids)


def test_analysis_requires_all_registered_slots_instead_of_balanced_partial_runs(tmp_path):
    from evaluation.agent_ab_analysis import analyze
    ab.save(tmp_path / "experiment.json", {"schedule": [["case-11", "baseline"], ["case-11", "rag"]]})
    assert not analyze(tmp_path)["complete"]


def test_passing_candidate_is_not_automatically_a_workflow_success():
    patch = "actual model candidate"
    result = {"state": {"status": "needs_input", "patch_hash": ab.bench.digest(patch)},
              "container_cleanup": True, "workspace_cleanup": True}
    assert not ab.primary_success(result, patch, True)
    result["state"]["status"] = "reviewable"
    assert ab.primary_success(result, patch, True)
    assert not ab.primary_success(result, patch + "changed", True)
    assert not ab.primary_success(result, patch, False)
    result["workspace_cleanup"] = False
    assert not ab.primary_success(result, patch, True)


def test_missing_candidate_never_executes_tests_or_uses_reference(tmp_path, monkeypatch):
    record = json.loads((ab.bench.ROOT / "benchmark.json").read_text(encoding="utf-8"))["cases"][0]
    monkeypatch.setattr(ab, "run_execution", lambda *a, **k: pytest.fail("Empty patch must not run reference"))
    application, tests = ab.score_candidate(record, "", tmp_path / "source", tmp_path / "evidence")
    assert application["reason"] == "empty_patch"
    assert not ab.bench.passed_group(tests, "test_existing", 3)


def test_same_total_success_can_hide_paired_regressions():
    result = ab.summarize([(True, True), (True, False), (False, True), (False, False)])
    assert result["uplift_count"] == 0
    assert result["baseline_only_success"] == result["rag_only_success"] == 1
    assert result["mcnemar_exact_two_sided_p"] == 1


def test_completed_failed_slot_is_not_selectively_rerun(tmp_path, monkeypatch):
    directory = tmp_path / "rag" / "case-01"
    ab.save(directory / "result.json", {"state": {"status": "failed"}})
    monkeypatch.setattr(ab, "run_process", lambda *a, **k: pytest.fail("Must not rerun failed slot"))
    with pytest.raises(ValueError, match="missing artifacts"):
        ab.launch(tmp_path, "case-01", "rag")
    (directory / "trace.jsonl").write_text('{"operation":"model.request"}', encoding="utf-8")
    ab.export_artifacts(tmp_path, "rag", "case-01", directory, "")
    ab.launch(tmp_path, "case-01", "rag")


def test_plan_rejects_hidden_file_added_to_agent_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "runtime_identity", lambda: {})
    directory = tmp_path / "inputs" / "case-01"
    directory.mkdir(parents=True)
    (directory / "target.py").write_text("pass", encoding="utf-8")
    benchmark = (ab.bench.ROOT / "benchmark.json").read_text(encoding="utf-8")
    (tmp_path / "benchmark.json").write_text(benchmark, encoding="utf-8")
    for arm in ("rag", "baseline"):
        (tmp_path / f"{arm}.sqlite3").write_bytes(b"fixture")
    plan = {"runtime": {}, "runner_sha256": ab.sha(ab.Path(ab.__file__)),
            "dataset_sha256": ab.bench.digest(benchmark),
            "inputs": [{"case_id": "case-01", "files": {"target.py": ab.sha(directory / "target.py")}}],
            "corpus_sha256": ab.sha(tmp_path / "rag.sqlite3"),
            "empty_corpus_sha256": ab.sha(tmp_path / "baseline.sqlite3")}
    ab.save(tmp_path / "experiment.json", plan)
    ab.save(tmp_path / "plan-hash.json", {"sha256": ab.sha(tmp_path / "experiment.json")})
    assert ab.verify_plan(tmp_path) == plan
    (directory / "test_acceptance.py").write_text("hidden", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected file"):
        ab.verify_plan(tmp_path)


def test_failure_attribution_requires_actual_quote_mismatch():
    result = {"state": {"status": "needs_input", "reproduction": {
        "status": "需要用户确认", "execution": {"stdout": "observed False", "stderr": ""}}}}
    comparison = {"comparison": "matched", "evidence": "This is a summary of observed False", "question": ""}
    def events():
        return [{"operation": "model.generate", "event": "succeeded", "result": {"text": json.dumps(comparison)}}]
    assert classify(result, events()) == "reproduction_exact_quote_rejected"
    comparison["evidence"] = "observed False"
    assert classify(result, events()) == "awaiting_clarification_or_evidence"
    comparison.update(evidence="summary", comparison="inconclusive")
    assert classify(result, events()) == "awaiting_clarification_or_evidence"
    result["state"]["reproduction"]["execution"]["stderr"] = "FileNotFoundError: /task/target.py"
    assert classify(result, events()) == "reproduction_script_wrong_container_path"
    result["state"]["reproduction"]["execution"]["stderr"] = "FileNotFoundError: unrelated.txt"
    assert classify(result, events()) == "awaiting_clarification_or_evidence"
    result["state"]["reproduction"]["execution"]["stderr"] = "ValueError: expected ':' after conversion specifier"
    assert classify(result, events()) == "reproduction_script_format_error"


def test_nested_test_definition_is_distinguished_from_a_weak_executed_test():
    reproduction = {"status": "已复现，与报告一致", "regression": {
        "baseline": {"valid": True, "return_code": 0},
        "test_code": "def test_reported_defect():\n    def test_inner():\n        assert False\n"}}
    result = {"state": {"status": "needs_input", "reproduction": reproduction}}
    assert classify(result, []) == "regression_nested_function_not_executed"
    reproduction["regression"]["test_code"] += "    test_inner()\n"
    assert classify(result, []) == "regression_did_not_fail_on_baseline"
    reproduction["regression"]["baseline"]["valid"] = False
    assert classify(result, []) == "regression_baseline_invalid"
