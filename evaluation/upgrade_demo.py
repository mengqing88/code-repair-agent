"""A deterministic synthetic demo of upgrade evaluation; no model/API calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from evaluation import upgrade_gate as gate


def _put(path, value):
    gate.write_once(Path(path), gate.canonical(value))


def fixture_configuration(model):
    return {"program": {"runtime": {"version": "synthetic-upgrade-demo-v1"},
                        "container": {"status": "synthetic_no_external_execution"}},
            "models": {"model": model}, "prompts": {"version": "demo-v1"},
            "tools": {"local": [], "mcp": {"configuration": {"servers": []}}},
            "retrieval": {"sha256": gate.sha256(b"no-knowledge-in-demo")},
            "settings": {"values": {"MODEL": model}}}


def example_spec():
    checks = {metric: [{"artifact": "independent-checks.json", "pointer": f"/{metric}", "equals": True}]
              for metric in gate.ORACLE_METRICS | {"safety_ok"}}
    return {"experiment_id": "synthetic-upgrade-demo", "purpose": "model", "evidence_kind": "synthetic",
            "variants": {"baseline": {"version": "demo-old", "configuration_ref": "baseline-config.json"},
                         "candidate": {"version": "demo-new", "configuration_ref": "candidate-config.json"}},
            "allowed_changes": ["/models", "/settings/values"],
            "repetitions": 3, "order_seed": 29, "protected_groups": ["critical", "ordinary"],
            "expected_pause_cases": [], "max_identical_tool_calls": 3, "oracle": checks,
            "trace_policy": {"allowed_tools": [], "required_tools": [], "required_nodes": [], "parameter_schemas": {}},
            "measurements": {name: {"artifact": "independent-checks.json", "pointer": f"/{name}"}
                             for name in ("regression_passed", "regression_total")},
            "budget": {"runtime_seconds": 120, "tokens": 20000, "cost_yuan": 0.1, "repair_attempts": 10},
            "thresholds": {"min_repair_success_delta": 0, "max_success_to_failure_pairs": 0,
                           "max_new_safety_violations": 0, "max_new_contract_failures": 0,
                           "max_new_regression_failures": 0, "max_new_false_accepts": 0,
                           "max_new_false_pauses": 0, "max_group_success_drop": 0,
                           "max_cost_ratio": 1.2, "max_p95_latency_ratio": 1.2}}


def run_demo(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    _put(directory / "baseline-config.json", fixture_configuration("synthetic-old"))
    _put(directory / "candidate-config.json", fixture_configuration("synthetic-new"))
    _put(directory / "spec.json", example_spec())
    _put(directory / "dataset.json", {"version": "synthetic-demo-v1", "cases": [
        {"case_id": "critical", "group": "critical", "input": [2, 1], "expected": 3},
        {"case_id": "ordinary-1", "group": "ordinary", "input": [4, 5], "expected": 9},
        {"case_id": "ordinary-2", "group": "ordinary", "input": [7, 2], "expected": 9}]})
    plan_dir = directory / "experiment"
    registered = gate.register_experiment(directory / "spec.json", directory / "dataset.json", plan_dir)

    def runner(request):
        case = request["case"]
        a, b = case["input"]
        # Execute actual Python fixture behavior and assert its result independently.
        if request["variant"] == "baseline":
            actual = a + b if case["case_id"] == "critical" else a - b
        else:
            actual = a - b if case["case_id"] == "critical" else a + b
        success = actual == case["expected"]
        task_id = uuid4().hex
        task_dir = directory / "raw-runs" / task_id
        manifest = request["configuration"]
        manifest["task"] = {"task_id": task_id, "input_sha256": gate.sha256(gate.canonical(case["input"])),
            "source_fingerprint": {"fixture": "synthetic-demo-v1"}, "memory": [],
            "budget": {"seconds": 120, "tokens": 20000, "cost": 0.1, "attempts": 10}}
        _put(task_dir / "execution-manifest.json", manifest)
        checks = {name: True for name in gate.ORACLE_METRICS | {"safety_ok"}}
        checks.update(repair_success=success, regression_passed=3, regression_total=3,
                      actual=actual, expected=case["expected"])
        _put(task_dir / "independent-checks.json", checks)
        _put(task_dir / "result.json", {"evidence_kind": "synthetic"})
        started = datetime.now(timezone.utc)
        base = {"task_id": task_id, "manifest_hash": gate.sha256(gate.canonical(manifest))}
        events = [{**base, "operation": "task.create", "event": "completed", "timestamp": started.isoformat()},
                  {**base, "operation": "workflow", "event": "finished",
                   "timestamp": (started + timedelta(seconds=1)).isoformat(),
                   "details": {"reason": "synthetic_fixture_completed"},
                   "result": {"status": "reviewable" if success else "failed",
                              "accounting": {"cost_complete": True, "cost_observed_yuan": 0.01}}}]
        gate.write_once(task_dir / "trace.jsonl", b"".join(gate.canonical(e) + b"\n" for e in events))
        return {"result_path": task_dir / "result.json", "task_directory": task_dir}

    gate.run_pairs(plan_dir, runner)
    report = gate.evaluate_upgrade(plan_dir)
    return {"evidence_kind": "synthetic", "plan_sha256": registered["plan_sha256"],
            "report_path": str(plan_dir / "report.json"), "passed": report["passed"],
            "expected_gate_rejection": not report["passed"], "failed_checks": report["failed_checks"],
            "success_delta": report["metrics"]["repair_success_delta"],
            "release_eligible": report["release_eligible"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run_demo(parser.parse_args().output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["expected_gate_rejection"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
