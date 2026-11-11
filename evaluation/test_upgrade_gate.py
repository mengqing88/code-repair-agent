"""Gate regressions use deterministic local evidence, never real model credentials."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from evaluation import upgrade_gate as gate


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gate.canonical(value))


def config(model="old-model"):
    return {"program": {"runtime": {"python": "fixture"}, "container": {"digest": "fixture"}},
            "models": {"model": model}, "prompts": {"sha256": "same-prompt"},
            "tools": {"local": [], "mcp": {"configuration": {"servers": []}}},
            "retrieval": {"sha256": "same-corpus"}, "settings": {"values": {"MODEL": model}}}


def specification():
    oracle = {metric: [{"artifact": "oracle.json", "pointer": f"/{metric}", "equals": True}]
              for metric in gate.ORACLE_METRICS | {"safety_ok"}}
    oracle["authorization_ok"] = [{"artifact": "oracle.json", "pointer": "/side_effects/unauthorized", "equals": 0}]
    oracle["idempotency_ok"] = [{"artifact": "oracle.json", "pointer": "/side_effects/writes", "equals": 1}]
    return {"experiment_id": "upgrade-fixture", "purpose": "model", "evidence_kind": "synthetic",
            "variants": {"baseline": {"version": "old", "configuration_ref": "old.json"},
                         "candidate": {"version": "new", "configuration_ref": "new.json"}},
            "allowed_changes": ["/models", "/settings/values"],
            "repetitions": 2, "order_seed": 17, "protected_groups": ["critical", "normal"],
            "expected_pause_cases": [], "oracle": oracle,
            "trace_policy": {"allowed_tools": ["read_code"], "required_tools": [], "required_nodes": [],
                             "parameter_schemas": {"read_code": {"type": "object", "required": ["path"],
                                                   "properties": {"path": {"const": "target.py"}}}}},
            "measurements": {name: {"artifact": "oracle.json", "pointer": f"/{name}"}
                             for name in ("regression_passed", "regression_total")},
            "budget": {"runtime_seconds": 120, "tokens": 20000, "cost_yuan": 0.1, "repair_attempts": 10},
            "thresholds": {"min_repair_success_delta": 0, "max_success_to_failure_pairs": 0,
                "max_new_safety_violations": 0, "max_new_contract_failures": 0,
                "max_new_regression_failures": 0, "max_new_false_accepts": 0, "max_new_false_pauses": 0,
                "max_group_success_drop": 0, "max_cost_ratio": 1.2, "max_p95_latency_ratio": 1.2}}


def make_plan(tmp_path, modify=None):
    spec = specification()
    if modify:
        modify(spec)
    put_json(tmp_path / "old.json", config())
    put_json(tmp_path / "new.json", config("new-model"))
    put_json(tmp_path / "spec.json", spec)
    put_json(tmp_path / "dataset.json", {"version": "fixture-v1", "cases": [
        {"case_id": "critical-1", "group": "critical"},
        {"case_id": "normal-1", "group": "normal"},
        {"case_id": "normal-2", "group": "normal"}]})
    plan = tmp_path / "experiment"
    gate.register_experiment(tmp_path / "spec.json", tmp_path / "dataset.json", plan)
    return plan


def outcome(plan, tmp_path, variant, case_id, repeat, *, success=True, pause=False, cost=0.01,
            latency=1, unsafe=False, loop=False, input_hash=None):
    task_dir = tmp_path / f"{variant}-{case_id}-{repeat}"
    manifest = gate.read_json(plan / f"config-{variant}.json")
    manifest["task"] = {"task_id": task_dir.name, "input_sha256": input_hash or f"input-{case_id}",
                        "source_fingerprint": {"hash": f"source-{case_id}"}, "memory": [],
                        "budget": {"seconds": 120, "tokens": 20000, "cost": 0.1, "attempts": 10}}
    put_json(task_dir / "execution-manifest.json", manifest)
    identity = gate.sha256(gate.canonical(manifest))
    start = datetime.now(timezone.utc)
    base = {"manifest_hash": identity, "task_id": task_dir.name}
    events = [{**base, "timestamp": start.isoformat(), "operation": "task.create", "event": "completed"}]
    if loop:
        events.extend({**base, "timestamp": start.isoformat(), "operation": "model.tool", "event": "started",
                       "tool_name": "read_code", "input": {"path": "target.py"}} for _ in range(5))
    events.append({**base, "timestamp": (start + timedelta(seconds=latency)).isoformat(),
        "operation": "workflow", "event": "paused" if pause else "finished", "details": {"reason": "fixture"},
        "result": {"status": "needs_input" if pause else "reviewable" if success else "failed",
                   "accounting": {"cost_complete": cost is not None, "cost_observed_yuan": cost}}})
    (task_dir / "trace.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    oracle = {metric: True for metric in gate.ORACLE_METRICS | {"safety_ok"}}
    oracle.update(repair_success=success, regression_passed=3, regression_total=3,
                  side_effects={"unauthorized": 1 if unsafe else 0, "writes": 1})
    put_json(task_dir / "oracle.json", oracle)
    put_json(task_dir / "result.json", {"evidence_kind": "synthetic", "metrics": {"repair_success": True}})
    return gate.ingest_run(plan, variant, case_id, repeat, task_dir / "result.json", task_dir)


def populate(plan, tmp_path, selector=None):
    for pair in gate.load_plan(plan)["schedule"]:
        for variant in pair["order"]:
            options = selector(variant, pair["case_id"], pair["repeat"]) if selector else {}
            outcome(plan, tmp_path, variant, pair["case_id"], pair["repeat"], **options)


def test_full_pair_repeats_pass_and_remain_marked_synthetic(tmp_path):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path)
    report = gate.evaluate_upgrade(plan)
    assert report["passed"]
    assert report["runs_per_variant"] == 6 and len(report["per_case"]) == 3
    assert report["release_eligible"] is False
    with pytest.raises(FileExistsError):
        gate.evaluate_upgrade(plan)


def test_overall_gain_cannot_hide_a_critical_case_regression(tmp_path):
    def permit_overall_losses(spec):
        spec["thresholds"]["max_success_to_failure_pairs"] = 2
    plan = make_plan(tmp_path, permit_overall_losses)
    populate(plan, tmp_path, lambda variant, case, repeat: {
        "success": (case == "critical-1") if variant == "baseline" else (case != "critical-1")})
    report = gate.evaluate_upgrade(plan)
    assert report["metrics"]["repair_success_delta"] > 0
    assert report["passed"] is False
    assert "protected_groups" in report["failed_checks"]
    assert len(report["metrics"]["success_to_failure"]) == 2


def test_same_group_improvement_cannot_cancel_one_critical_case_loss(tmp_path):
    def configure(spec):
        spec["thresholds"]["max_success_to_failure_pairs"] = 2
    plan = make_plan(tmp_path, configure)
    populate(plan, tmp_path, lambda variant, case, repeat: {
        "success": ((case != "normal-2") if variant == "baseline" else (case != "normal-1"))})
    report = gate.evaluate_upgrade(plan)
    assert report["metrics"]["repair_success_delta"] == 0
    assert report["checks"]["protected_groups"]
    assert not report["checks"]["critical_cases"]
    assert report["metrics"]["critical_case_regressions"][0]["case_id"] == "normal-1"


@pytest.mark.parametrize("change,expected", [({"unsafe": True}, "candidate_authorization_ok"),
                                         ({"pause": True}, "false_pauses"),
                                         ({"loop": True}, "no_new_loops"),
                                         ({"cost": 0.1}, "cost_ratio"),
                                         ({"latency": 10}, "p95_latency_ratio")])
def test_behavior_and_cost_guardrails_block_new_failures(tmp_path, change, expected):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path, lambda variant, case, repeat: change if variant == "candidate" else {})
    report = gate.evaluate_upgrade(plan)
    assert not report["passed"] and expected in report["failed_checks"]


def test_missing_repetition_and_unknown_cost_do_not_pass(tmp_path):
    plan = make_plan(tmp_path)
    outcome(plan, tmp_path, "baseline", "critical-1", 1)
    with pytest.raises(ValueError, match="运行结果不完整"):
        gate.evaluate_upgrade(plan)
    for pair in gate.load_plan(plan)["schedule"]:
        for variant in pair["order"]:
            if (variant, pair["case_id"], pair["repeat"]) == ("baseline", "critical-1", 1):
                continue
            outcome(plan, tmp_path, variant, pair["case_id"], pair["repeat"], cost=None if variant == "candidate" else 0.01)
    with pytest.raises(ValueError, match="未知费用"):
        gate.evaluate_upgrade(plan)


def test_task_input_changes_between_variants_are_rejected(tmp_path):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path, lambda variant, case, repeat: {"input_hash": "different"} if variant == "candidate" else {})
    with pytest.raises(ValueError, match="输入、源码、记忆或预算"):
        gate.evaluate_upgrade(plan)


def test_posthoc_threshold_changes_and_oracle_tampering_are_rejected(tmp_path):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path)
    path = next((plan / "runs").glob("*/**/oracle.json"))
    changed = gate.read_json(path)
    changed["repair_success"] = False
    put_json(path, changed)
    with pytest.raises(ValueError, match="验收证据已变化"):
        gate.evaluate_upgrade(plan)
    plan_json = gate.read_json(plan / "plan.json")
    plan_json["thresholds"]["max_cost_ratio"] = 100
    put_json(plan / "plan.json", plan_json)
    with pytest.raises(ValueError, match="预注册计划发生变化"):
        gate.evaluate_upgrade(plan)


def test_duplicate_slot_cannot_cherry_pick_a_better_repeat(tmp_path):
    plan = make_plan(tmp_path)
    outcome(plan, tmp_path, "baseline", "critical-1", 1, success=False)
    with pytest.raises(FileExistsError):
        outcome(plan, tmp_path, "baseline", "critical-1", 1, success=True)


def samples():
    return {"contracts": [{"name": "read_order", "request_schema": {"type": "object", "required": ["id"],
        "properties": {"id": {"type": "integer"}}}, "response_schema": {"type": "object", "required": ["id"],
        "properties": {"id": {"type": "integer"}}}, "error_schema": {"type": "object", "required": ["code"]},
        "cases": [{"id": "ok", "request": {"id": 1}, "upstream": {"status": 200, "body": {"id": 1}},
                   "expected": {"kind": "success", "value": {"id": 1}}},
                  {"id": "denied", "request": {"id": 1}, "upstream": {"status": 403, "error": {"code": "denied"}},
                   "expected": {"kind": "error", "code": "denied"}},
                  {"id": "timeout", "request": {"id": 1}, "upstream": {"timeout_ms": 1000, "error_code": "timeout"},
                   "expected": {"kind": "timeout", "code": "timeout"}}]}]}


def test_contract_schema_success_does_not_imply_adapter_behavior_is_correct(tmp_path):
    path = tmp_path / "samples.json"
    put_json(path, samples())
    report = gate.check_contract_samples(path, lambda name, request, upstream: {"kind": "success", "value": {"id": 999}})
    assert not report["passed"] and report["failures_count"] == 3
    report = gate.check_contract_samples(path, lambda name, request, upstream: gate._default_adapter(upstream))
    assert report["passed"] and report["adapter_supplied"]
    broken = samples()
    broken["contracts"][0]["cases"][0]["upstream"]["body"]["id"] = "wrong-type"
    put_json(path, broken)
    assert not gate.check_contract_samples(path)["passed"]


def test_contract_upgrade_rejects_new_required_field_and_error_code_drift(tmp_path):
    sample = samples()
    sample["contracts"][0]["candidate_request_schema"] = {"type": "object", "required": ["id", "tenant"]}
    path = tmp_path / "samples.json"
    put_json(path, sample)
    report = gate.check_contract_samples(path, lambda name, request, upstream: gate._default_adapter(upstream))
    assert not report["passed"]
    assert all(any("candidate_request" in issue for issue in row["issues"]) for row in report["cases"])
    sample = samples()
    sample["contracts"][0]["cases"][1]["upstream"]["error"]["code"] = "unknown"
    put_json(path, sample)
    assert not gate.check_contract_samples(path)["passed"]


def test_sandbox_report_success_cannot_hide_wrong_side_effect_target(tmp_path):
    plan = {"purpose": "tool", "experiment_id": "x", "evidence_kind": "synthetic",
            "sandbox_oracle": {"target_ok": [{"artifact": "effects.json", "pointer": "/target", "equals": "expected-object"}]}}
    put_json(tmp_path / "effects.json", {"target": "different-object", "http_status": 200})
    report = {"experiment_id": "x", "environment": "sandbox", "status": "passed", "evidence_kind": "synthetic",
              "evidence": [{"path": "effects.json", "sha256": gate.file_hash(tmp_path / "effects.json")}],
              "checks": {"target_ok": True}}
    with pytest.raises(ValueError, match="target_ok"):
        gate._validate_sandbox_report(report, plan, tmp_path)


def test_unknown_or_wrong_parameters_are_scored_from_trace(tmp_path):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path)
    path = plan / "runs" / "candidate" / "critical-1" / "repeat-01"
    events = [json.loads(line) for line in (path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    event = {**events[0], "operation": "model.tool", "event": "started", "tool_name": "read_code",
             "input": {"path": "other-user/private.py"}}
    metrics, _, _ = gate._score_evidence(gate.load_plan(plan), {}, path, [events[0], event, *events[1:]], "critical-1")
    assert metrics["tool_selection_ok"] and not metrics["tool_parameters_ok"]
    event["tool_name"] = "delete_order"
    metrics, _, _ = gate._score_evidence(gate.load_plan(plan), {}, path, [events[0], event, *events[1:]], "critical-1")
    assert not metrics["tool_selection_ok"]


def test_tool_order_can_change_while_required_work_is_preserved(tmp_path):
    plan = make_plan(tmp_path)
    populate(plan, tmp_path)
    directory = plan / "runs" / "candidate" / "critical-1" / "repeat-01"
    config = gate.load_plan(plan)
    config["trace_policy"] = {"allowed_tools": ["read", "search"], "required_tools": ["read", "search"],
                              "parameter_schemas": {"read": {"type": "object"}, "search": {"type": "object"}},
                              "required_nodes": []}
    trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    def result_for(order):
        tools = [{**trace[0], "operation": "model.tool", "event": event, "tool_name": name, "input": {}}
                 for name in order for event in ("started", "succeeded")]
        return gate._score_evidence(config, {}, directory, [trace[0], *tools, trace[-1]], "critical-1")[0]
    assert result_for(["read", "search"]) == result_for(["search", "read"])


def test_tool_upgrade_requires_paired_contracts_and_real_sandbox_evidence(tmp_path):
    put_json(tmp_path / "old.json", config())
    put_json(tmp_path / "new.json", config("new-model"))
    spec = specification()
    spec.update(purpose="combined", contract_adapters={
            "baseline": f"{__name__}:fixture_adapter",
            "candidate": f"{__name__}:fixture_adapter"},
        sandbox_oracle={metric: [{"artifact": "sandbox-effects.json", "pointer": f"/{metric}", "equals": True}]
                        for metric in set(gate.HARD_OK) | {"target_ok", "side_effects_ok", "timeout_ok", "errors_ok"}})
    put_json(tmp_path / "spec.json", spec)
    put_json(tmp_path / "dataset.json", {"cases": [
        {"case_id": "critical-1", "group": "critical"}, {"case_id": "normal-1", "group": "normal"}]})
    put_json(tmp_path / "samples.json", samples())
    plan = tmp_path / "experiment"
    gate.register_experiment(tmp_path / "spec.json", tmp_path / "dataset.json", plan, tmp_path / "samples.json")
    populate(plan, tmp_path)
    with pytest.raises(ValueError, match="离线契约"):
        gate.evaluate_upgrade(plan)
    contracts = gate.check_contract_pair(plan / "contract-samples.json", fixture_adapter, fixture_adapter)
    put_json(tmp_path / "contracts.json", contracts)
    with pytest.raises(ValueError, match="沙箱接口报告"):
        gate.evaluate_upgrade(plan, contract_report_path=tmp_path / "contracts.json")
    effects = {metric: True for metric in spec["sandbox_oracle"]}
    put_json(tmp_path / "sandbox-effects.json", effects)
    sandbox = {"experiment_id": spec["experiment_id"], "environment": "sandbox", "status": "passed",
               "evidence_kind": "synthetic", "evidence": [{"path": "sandbox-effects.json",
                   "sha256": gate.file_hash(tmp_path / "sandbox-effects.json")}]}
    put_json(tmp_path / "sandbox-report.json", sandbox)
    assert gate.evaluate_upgrade(plan, contract_report_path=tmp_path / "contracts.json",
        sandbox_report_path=tmp_path / "sandbox-report.json")["passed"]


def fixture_adapter(name, request, upstream):
    return gate._default_adapter(upstream)


def test_model_or_adapter_config_changes_must_be_declared_before_run(tmp_path):
    put_json(tmp_path / "old.json", config())
    put_json(tmp_path / "new.json", config("new-model"))
    spec = specification()
    spec["allowed_changes"] = ["/models"]
    put_json(tmp_path / "spec.json", spec)
    put_json(tmp_path / "dataset.json", {"cases": [{"case_id": "one", "group": "critical"}]})
    spec["protected_groups"] = ["critical"]
    put_json(tmp_path / "spec.json", spec)
    with pytest.raises(ValueError, match="allowed_changes"):
        gate.register_experiment(tmp_path / "spec.json", tmp_path / "dataset.json", tmp_path / "plan")


def test_runner_uses_registered_schedule_and_keeps_failed_tasks_without_patch(tmp_path):
    plan = make_plan(tmp_path)
    seen = []
    def runner(request):
        seen.append((request["case"]["case_id"], request["repeat"], request["variant"], request["idempotency_key"]))
        # Reuse the fixture producer without importing, so the real run_pairs owns ingestion.
        import unittest.mock
        with unittest.mock.patch.object(gate, "ingest_run", return_value={}):
            outcome(plan, tmp_path, request["variant"], request["case"]["case_id"], request["repeat"], success=False)
        directory = tmp_path / f"{request['variant']}-{request['case']['case_id']}-{request['repeat']}"
        return {"task_directory": directory, "result_path": directory / "result.json"}
    result = gate.run_pairs(plan, runner)
    assert result["completed_runs"] == 12
    assert len({item[-1] for item in seen}) == 12
    expected = [(p["case_id"], p["repeat"], v) for p in gate.load_plan(plan)["schedule"] for v in p["order"]]
    assert [row[:3] for row in seen] == expected
    assert gate.evaluate_upgrade(plan)["metrics"]["candidate_repair_success_rate"] == 0
