"""用生产补丁审查器评估真实模型对短小合成补丁的判断质量。"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from code_repair.adversarial_review import review_patch, static_findings
from code_repair.llm_client import _chat_configuration, _estimate_cost_yuan, ask_model_with_usage
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, create_checkpoint_workspace, remove_checkpoint_workspace
from code_repair.resilience import Fault, execution_scope


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evaluation" / "adversarial_review_cases_20261112.json"
DEFAULT_OUTPUT = ROOT / "work" / "evaluation" / "real-model-review-quality-20261112.json"
TEMP_ROOT = ROOT.parent / "CodexTemp" / "repair-review-quality"
MAX_TOKENS = 20_000
MAX_RUNTIME_SECONDS = 120
REPEATS = 2
EXPECTED_CASES = 4


def load_cases() -> list[dict[str, object]]:
    """读取并检查预设标签、源码及案例 ID。"""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise ValueError("审查集必须包含四个案例")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("审查案例 ID 不能重复")
    for case in cases:
        if not isinstance(case.get("report"), str) or not isinstance(case.get("baseline"), str):
            raise ValueError(f"案例 {case.get('id')} 缺少报告或基线源码")
        if not isinstance(case.get("patched"), str) or not isinstance(case.get("expected_approved"), bool):
            raise ValueError(f"案例 {case.get('id')} 缺少补丁或预期结论")
    return cases


def configure_temp_dir() -> None:
    """把本进程及其子进程的临时文件统一放到 F 盘评测临时目录。"""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = str(TEMP_ROOT)
    tempfile.tempdir = str(TEMP_ROOT)


def build_candidate(case: dict[str, object], trial_root: Path) -> tuple[RepairToolbox, dict, str]:
    """在独立临时 Git 副本中应用一个补丁，并构造生产审查器需要的状态。"""
    source = trial_root / "source"
    workspace = trial_root / "workspace"
    source.mkdir(parents=True)
    (source / "module.py").write_text(str(case["baseline"]), encoding="utf-8")
    (source / "test_module.py").write_text("def test_review_fixture_is_collectable():\n    assert True\n", encoding="utf-8")
    revision = create_checkpoint_workspace(source, workspace)
    toolbox = RepairToolbox(workspace, allowed_paths=("module.py",),
                            test_targets=("test_module.py",), checkpoint_revision=revision,
                            execution_backend="host", mcp_client=object())
    patch = "".join(difflib.unified_diff(
        str(case["baseline"]).splitlines(keepends=True),
        str(case["patched"]).splitlines(keepends=True),
        fromfile="a/module.py", tofile="b/module.py",
    ))
    findings = static_findings(str(case["baseline"]), str(case["patched"]), "module.py")
    if findings:
        raise ValueError(f"案例 {case['id']} 被静态规则拦截，不能计为模型判断")
    toolbox.apply_model_patch(patch)
    state = create_initial_state(
        f"review-quality-{case['id']}",
        {"type": "python_file", "path": str(source / "module.py")},
        str(case["report"]),
    )
    state.update(patch_diff=patch, patch_hash=hashlib.sha256(patch.encode("utf-8")).hexdigest())
    return toolbox, state, patch


def _model_request_count(events: list[dict]) -> int:
    """统计模型 Responses API 的实际请求尝试数，包含格式纠错和网络重试。"""
    return sum(event.get("operation") == "model.responses" and event.get("event") == "started"
               for event in events)


def _citation_valid(checks: list[dict], sources: dict[str, str]) -> bool:
    """确认模型每条引用都能在真实基线或补丁源码中逐字找到。"""
    normalize = lambda text: text.replace("\r\n", "\n").replace("\r", "\n")
    return bool(checks) and all(
        check.get("path") == "module.py"
        and any(normalize(check.get("quote", "")) in normalize(source) for source in sources.values())
        for check in checks
    )


def _evaluate_trial(case: dict[str, object], repeat: int, temp_root: Path, context) -> dict[str, object]:
    """运行一次生产审查，保存结论、引用校验、Token 和延迟。"""
    temporary = tempfile.TemporaryDirectory(prefix=f"{case['id']}-{repeat}-", dir=temp_root)
    trial_dir = Path(temporary.name)
    workspace = trial_dir / "workspace"
    revision = None
    started_tokens = context.tokens
    started_requests = _model_request_count(context.events)
    started_at = time.perf_counter()
    result: dict[str, object] = {
        "case_id": case["id"], "category": case["category"], "repeat": repeat,
        "expected_approved": case["expected_approved"],
        "target_aspect": case["target_aspect"],
    }
    try:
        toolbox, state, _ = build_candidate(case, trial_dir)
        revision = toolbox.checkpoint_revision
        reviewed = review_patch(state, toolbox, model_call=ask_model_with_usage)
        checks = reviewed.get("checks", [])
        source_pair = {
            "baseline": str(case["baseline"]),
            "patched": str(case["patched"]),
        }
        citation_valid = _citation_valid(checks, source_pair)
        target_verdict = next(
            (check.get("verdict") for check in checks if check.get("aspect") == case["target_aspect"]),
            None,
        ) if case["target_aspect"] else None
        result.update({
            "completed": True,
            "approved": reviewed["approved"],
            "decision_correct": reviewed["approved"] is case["expected_approved"],
            "target_aspect_verdict": target_verdict,
            "target_aspect_detected": target_verdict == "fail" if case["target_aspect"] else None,
            "citation_valid": citation_valid,
            "summary": str(reviewed.get("summary", ""))[:500],
            "checks": [{key: check.get(key) for key in ("aspect", "verdict", "path", "quote", "reasoning")}
                       for check in checks],
        })
    except Exception as error:
        result.update({"completed": False, "error_code": getattr(error, "code", type(error).__name__)})
    finally:
        try:
            if revision and workspace.exists():
                remove_checkpoint_workspace(workspace, revision)
            temporary.cleanup()
            result["workspace_cleanup"] = True
        except Exception:
            result["workspace_cleanup"] = False
        result["elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
        result["tokens_observed"] = context.tokens - started_tokens
        result["model_request_attempts"] = _model_request_count(context.events) - started_requests
        result["token_usage_unknown"] = result["model_request_attempts"] > 0 and result["tokens_observed"] == 0
    return result


def summarize(results: list[dict[str, object]], context, model: str, fee_cap_enforced: bool) -> dict[str, object]:
    """汇总误放行、误拒绝、目标风险识别、引用和预算数据。"""
    completed = [item for item in results if item.get("completed")]
    flawed = [item for item in completed if item["expected_approved"] is False]
    safe = [item for item in completed if item["expected_approved"] is True]
    return {
        "status": "completed" if len(completed) == len(results) else "incomplete",
        "model": model,
        "dataset": CASES_PATH.name,
        "repeats_per_case": REPEATS,
        "metrics": {
            "completed_reviews": len(completed),
            "expected_reviews": EXPECTED_CASES * REPEATS,
            "completion_coverage": len(completed) / (EXPECTED_CASES * REPEATS),
            "decision_accuracy": (sum(item["decision_correct"] for item in completed) / len(completed)
                                  if completed else None),
            "false_accepts": sum(item["approved"] for item in flawed),
            "false_rejects": sum(not item["approved"] for item in safe),
            "target_risk_detection_rate": (
                sum(item["target_aspect_detected"] for item in flawed if item["target_aspect"])
                / sum(bool(item["target_aspect"]) for item in flawed)
                if any(item["target_aspect"] for item in flawed) else None
            ),
            "citation_validation_rate": (sum(item["citation_valid"] for item in completed) / len(completed)
                                         if completed else None),
        },
        "budget": {
            "max_runtime_seconds": MAX_RUNTIME_SECONDS,
            "elapsed_seconds": round(MAX_RUNTIME_SECONDS - context.remaining(), 3),
            "max_tokens": MAX_TOKENS,
            "tokens_used": context.tokens,
            "cost_cap_yuan": 0.10,
            "cost_cap_enforced": fee_cap_enforced,
            "cost_estimate_yuan": round(context.cost, 8) if fee_cap_enforced and context.cost_known else None,
            "cost_unknown": not fee_cap_enforced or not context.cost_known,
            "token_usage_unknown": not context.usage_known,
        },
        "model_request_attempts": _model_request_count(context.events),
        "results": results,
        "limits": [
            "四个合成案例、每例两次，样本很小且预期标签未独立复核。",
            "测量的是当前模型、提示词和审查实现；不能代表任意代码库或生产误放行率。",
            "费用费率未配置时，0.10 元不能成为硬限制；本次仅强制执行 20,000 Token 和 120 秒上限。",
        ],
    }


def main() -> int:
    """运行无模型预检或真实审查，并持续写入 F 盘结果文件。"""
    configure_temp_dir()
    parser = argparse.ArgumentParser(description="评测真实模型的独立补丁审查质量")
    parser.add_argument("--dry-run", action="store_true", help="仅检查样例和静态规则，不调用模型")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="结果 JSON 路径，默认位于 work/evaluation")
    args = parser.parse_args()
    cases = load_cases()
    if args.dry_run:
        checks = []
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        for case in cases:
            findings = static_findings(str(case["baseline"]), str(case["patched"]), "module.py")
            candidate_applied = False
            with tempfile.TemporaryDirectory(prefix=f"preflight-{case['id']}-", dir=TEMP_ROOT) as temporary:
                toolbox, _, _ = build_candidate(case, Path(temporary))
                candidate_applied = (toolbox.workspace_root / "module.py").read_text(encoding="utf-8") == case["patched"]
                if toolbox.checkpoint_revision:
                    remove_checkpoint_workspace(toolbox.workspace_root, toolbox.checkpoint_revision)
            checks.append({"case_id": case["id"], "static_findings": findings,
                           "candidate_applied": candidate_applied})
        print(json.dumps({"cases": len(cases), "model_calls": 0, "preflight": checks}, ensure_ascii=False, indent=2))
        return 0 if all(not item["static_findings"] and item["candidate_applied"] for item in checks) else 1

    output = args.output if args.output.is_absolute() else ROOT / args.output
    if output.exists():
        raise FileExistsError(f"拒绝覆盖已有评测结果：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    settings, _, _, model = _chat_configuration(None)
    fee_known = _estimate_cost_yuan(settings, 1000, 1000) is not None
    results: list[dict[str, object]] = []

    with execution_scope(MAX_RUNTIME_SECONDS, task_id="real-model-review-quality",
                         max_tokens=MAX_TOKENS, max_cost=0.10 if fee_known else None) as context:
        try:
            for case in cases:
                for repeat in range(1, REPEATS + 1):
                    result = _evaluate_trial(case, repeat, TEMP_ROOT, context)
                    results.append(result)
                    partial = summarize(results, context, model, fee_known)
                    output.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    if not result["completed"] and context.remaining() <= 0:
                        break
                if context.remaining() <= 0 or context.tokens >= MAX_TOKENS:
                    break
        except Fault as error:
            results.append({"completed": False, "error_code": error.code})
        finally:
            report = summarize(results, context, model, fee_known)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"output": str(output), "status": report["status"],
                      "metrics": report["metrics"], "budget": report["budget"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
