"""Explain real paired outcomes from persisted evidence, without calling services."""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path


def reproduction_comparison(events):
    for event in reversed(events):
        if event["operation"] != "model.generate" or event["event"] != "succeeded":
            continue
        try:
            value = json.loads(event.get("result", {}).get("text", ""))
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and "comparison" in value:
            return value
    return {}


def classify(result, events):
    state = result["state"]
    reproduction = state.get("reproduction") or {}
    comparison = reproduction_comparison(events)
    execution = reproduction.get("execution") or {}
    output = str(execution.get("stdout", "")) + str(execution.get("stderr", ""))
    quote = comparison.get("evidence")
    if (reproduction.get("status") == "需要用户确认" and comparison.get("comparison") == "matched"
            and not comparison.get("question") and isinstance(quote, str) and quote.strip() and quote not in output):
        return "reproduction_exact_quote_rejected"
    if state.get("error_info"):
        return state["error_info"]["code"]
    if reproduction.get("error_info"):
        return "reproduction." + reproduction["error_info"]["code"]
    if (state["status"] == "needs_input" and "FileNotFoundError" in output
            and "/task/target.py" in output):
        return "reproduction_script_wrong_container_path"
    if (state["status"] == "needs_input" and "ValueError: expected ':' after conversion specifier" in output):
        return "reproduction_script_format_error"
    baseline = (reproduction.get("regression") or {}).get("baseline")
    if state["status"] == "needs_input" and baseline:
        if not baseline.get("valid"):
            return "regression_baseline_invalid"
        if baseline.get("return_code") != 1:
            try:
                tree = ast.parse(reproduction["regression"]["test_code"])
                test = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "test_reported_defect")
                if len(test.body) == 1 and isinstance(test.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return "regression_nested_function_not_executed"
            except (KeyError, SyntaxError, StopIteration):
                pass
            return "regression_did_not_fail_on_baseline"
    if state["status"] == "needs_input":
        return "awaiting_clarification_or_evidence"
    return state["status"]


def analyze(output):
    records = []
    for arm in ("baseline", "rag"):
        for file in sorted((output / arm).glob("case-*/result.json")):
            result = json.loads(file.read_text(encoding="utf-8"))
            events = [json.loads(line) for line in (file.parent / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            started = {e["operation"] for e in events if e["event"] == "started"}
            responses = [e for e in events if e["operation"] == "model.response"]
            chat_tokens = sum(e.get("result", {}).get("total_tokens", 0) for e in events
                              if e["operation"] in ("model.generate", "model.tools") and e["event"] == "succeeded")
            embedding_tokens = sum(e.get("result", {}).get("tokens", 0) for e in events
                                   if e["operation"] == "embedding.result")
            reproduction = result["state"].get("reproduction") or {}
            records.append({"arm": arm, "case_id": result["case_id"], "status": result["state"]["status"],
                "classification": classify(result, events), "chat_tokens": chat_tokens,
                "embedding_tokens": embedding_tokens, "observed_total_tokens": result["model_tokens"],
                "charged_tokens_without_success_result": (None if result["model_tokens"] is None else
                    result["model_tokens"] - chat_tokens - embedding_tokens),
                "context_entered": "collect_context" in started, "patch_agent_entered": "propose_patch" in started,
                "validation_entered": "validate_patch" in started, "review_entered": "review_patch" in started,
                "reproduction_confirmed": reproduction.get("status") == "已复现，与报告一致",
                "script_execution_attempts": len(reproduction.get("execution_attempts", [])),
                "patch_model_responses": sum(e["operation"] == "model.tools" and e["event"] == "succeeded" for e in events),
                "tool_calls": sum(e["event"] == "started" and e.get("tool_name") == e["operation"] for e in events),
                "candidate_generated": bool(result.get("candidate_sha256")),
                "retrieved_cases": [{k: match.get(k) for k in ("case_id", "revision", "retrieval_mode", "decision")}
                                    for match in result["state"].get("case_matches", [])],
                "actual_model_ids": sorted({e.get("details", {}).get("model") for e in responses if e.get("details", {}).get("model")}),
                "container_cleanup": result.get("container_cleanup"), "workspace_cleanup": result.get("workspace_cleanup")})
    plan = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
    expected = {tuple(slot) for slot in plan["schedule"]}
    observed = {(record["case_id"], record["arm"]) for record in records}
    expected_per_arm = len(plan["schedule"]) // 2
    return {"complete": observed == expected and len(records) == len(plan["schedule"]),
            "expected_per_arm": expected_per_arm, "arms": {arm: {
        "completed": sum(r["arm"] == arm for r in records),
        "classifications": dict(Counter(r["classification"] for r in records if r["arm"] == arm)),
        **{key: sum(r[key] for r in records if r["arm"] == arm)
           for key in ("chat_tokens", "embedding_tokens", "context_entered", "patch_agent_entered", "validation_entered", "review_entered",
                       "reproduction_confirmed", "script_execution_attempts", "patch_model_responses", "tool_calls", "candidate_generated")},
        "tasks_with_retrieved_cases": sum(bool(r["retrieved_cases"]) for r in records if r["arm"] == arm),
        "cleanup_ok": sum(bool(r["container_cleanup"] and r["workspace_cleanup"]) for r in records if r["arm"] == arm),
        "observed_total_tokens": sum(r["observed_total_tokens"] or 0 for r in records if r["arm"] == arm),
        "charged_tokens_without_success_result": sum(r["charged_tokens_without_success_result"] or 0 for r in records if r["arm"] == arm),
    } for arm in ("baseline", "rag")}, "cases": records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.output.resolve())
    (args.output / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
