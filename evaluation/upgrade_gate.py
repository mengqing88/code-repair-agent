"""Pre-registered, paired regression gate for model and tool upgrades."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from copy import deepcopy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


ROOT = Path(__file__).resolve().parent
RUN_METRICS = (
    "repair_success", "regression_pass", "false_accept", "false_pause", "safety_violation",
    "preconditions_ok", "authorization_ok", "idempotency_ok", "data_isolation_ok",
    "tool_selection_ok", "tool_parameters_ok", "required_steps_ok",
)
HARD_OK = ("preconditions_ok", "authorization_ok", "idempotency_ok", "data_isolation_ok")
THRESHOLDS = {
    "min_repair_success_delta", "max_success_to_failure_pairs", "max_new_safety_violations",
    "max_new_contract_failures", "max_new_regression_failures", "max_new_false_accepts",
    "max_new_false_pauses", "max_group_success_drop", "max_cost_ratio", "max_p95_latency_ratio",
}
CONFIG_PATHS = ("/program/runtime", "/program/container", "/models", "/prompts", "/tools/local",
                "/tools/mcp/configuration", "/retrieval/sha256", "/settings/values")
OPTIONAL_CONFIG_PATHS = ("/tools/adapter_version", "/retrieval/engine", "/retrieval/embedding_model",
                         "/retrieval/reranker", "/retrieval/limit", "/retrieval/min_similarity",
                         "/settings/credentials", "/tools/permissions/external_write")
TRACE_METRICS = {"tool_selection_ok", "tool_parameters_ok", "required_steps_ok"}
ORACLE_METRICS = set(RUN_METRICS) - {"false_accept", "false_pause", "safety_violation"} - TRACE_METRICS


def pointer(value, path):
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("JSON Pointer 必须为空或以 / 开头")
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError(f"证据缺少字段 {path}") from None
    return value


def contained(root, name):
    original = Path(root) / name
    path = original.resolve()
    if (not path.is_relative_to(Path(root).resolve()) or not path.is_file()
            or any(part.is_symlink() for part in (original, *original.parents))):
        raise ValueError(f"证据路径必须是目录内的普通文件: {name}")
    return path


def _configuration(manifest):
    values = {path: sha256(canonical(pointer(manifest, path))) for path in CONFIG_PATHS}
    for path in OPTIONAL_CONFIG_PATHS:
        try:
            value = pointer(manifest, path)
        except ValueError:
            value = None
        values[path] = sha256(canonical(value))
    return values


def load_plan(directory):
    plan_file = Path(directory) / "plan.json"
    plan_bytes = plan_file.read_bytes()
    expected = (Path(directory) / "plan.sha256").read_text(encoding="ascii").strip()
    if sha256(plan_bytes) != expected:
        raise ValueError("预注册计划发生变化；必须新建实验，不能修改门槛后继续计分")
    plan = json.loads(plan_bytes)
    for variant, entry in plan["variants"].items():
        config_file = contained(directory, f"config-{variant}.json")
        if file_hash(config_file) != entry["configuration_sha256"]:
            raise ValueError("预注册配置快照已变化")
        if entry.get("tool_contract_sha256"):
            contract = contained(directory, f"tool-contract-{variant}.json")
            if file_hash(contract) != entry["tool_contract_sha256"]:
                raise ValueError("预注册工具契约快照已变化")
    return plan


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes())


def write_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False).encode("utf-8")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 JSON 文件 {path}: {type(error).__name__}") from None


def _finite_number(value, label, *, positive=False, allow_none=False):
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须是有限数字" + ("或 null" if allow_none else ""))
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{label} 必须是" + ("正数" if positive else "非负数"))


def _load_dataset(path: Path):
    data = read_json(path)
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("冻结任务集必须包含非空 cases 列表")
    identifiers = [case.get("case_id") for case in cases]
    if any(not isinstance(value, str) or not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("任务集 case_id 必须唯一且非空")
    if any(Path(value).name != value or "/" in value or "\\" in value or value in {".", ".."} for value in identifiers):
        raise ValueError("case_id 不能包含路径分隔符")
    groups = {}
    for case in cases:
        group = case.get("group")
        if not isinstance(group, str) or not group:
            raise ValueError(f"{case['case_id']} 缺少分组")
        groups.setdefault(group, []).append(case["case_id"])
    return data, cases, groups


def _validate_spec(spec, cases, groups, dataset_hash):
    if not isinstance(spec, dict):
        raise ValueError("实验注册内容必须是 JSON 对象")
    if not isinstance(spec.get("experiment_id"), str) or not spec["experiment_id"].strip():
        raise ValueError("必须提供 experiment_id")
    if spec.get("purpose") not in {"model", "tool", "combined"}:
        raise ValueError("purpose 必须是 model、tool 或 combined")
    if spec.get("evidence_kind") not in {"synthetic", "live"}:
        raise ValueError("evidence_kind 必须显式标记 synthetic 或 live")
    trace_policy = spec.get("trace_policy")
    if not isinstance(trace_policy, dict) or not {"allowed_tools", "required_tools", "parameter_schemas", "required_nodes"} <= set(trace_policy):
        raise ValueError("必须预注册 trace_policy 的 allowed_tools、required_tools、parameter_schemas、required_nodes")
    for field in ("allowed_tools", "required_tools", "required_nodes"):
        if not isinstance(trace_policy[field], list) or not all(isinstance(name, str) for name in trace_policy[field]):
            raise ValueError(f"trace_policy.{field} 必须是字符串列表")
    if not set(trace_policy["required_tools"]) <= set(trace_policy["allowed_tools"]):
        raise ValueError("required_tools 必须在 allowed_tools 中")
    if not isinstance(trace_policy["parameter_schemas"], dict) or set(trace_policy["parameter_schemas"]) != set(trace_policy["allowed_tools"]):
        raise ValueError("每个允许的工具都必须配置参数 Schema")
    for schema in trace_policy["parameter_schemas"].values():
        Draft202012Validator.check_schema(schema)
    oracle = spec.get("oracle")
    required_oracles = ORACLE_METRICS | {"safety_ok"}
    if not isinstance(oracle, dict) or set(oracle) != required_oracles:
        raise ValueError(f"oracle 必须冻结全部独立验收规则: {sorted(required_oracles)}")
    for metric, rules in oracle.items():
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"oracle.{metric} 必须至少有一条证据规则")
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("artifact"), str) or not rule["artifact"]:
                raise ValueError("oracle 规则必须固定 artifact 和 JSON Pointer")
            if not isinstance(rule.get("pointer"), str) or not rule["pointer"].startswith("/") or "equals" not in rule:
                raise ValueError("oracle 规则必须含 pointer 与 equals；不使用 Agent 自评")
            relative = Path(rule["artifact"])
            if relative.is_absolute() or ".." in relative.parts or relative.name in {"run.json", "execution-manifest.json", "trace.jsonl"}:
                raise ValueError("oracle artifact 必须使用独立且受限的相对证据路径")
    if not isinstance(spec.get("expected_pause_cases"), list) or not set(spec["expected_pause_cases"]) <= {c["case_id"] for c in cases}:
        raise ValueError("必须预注册 expected_pause_cases（无预期暂停时为空列表）")
    required_measurements = {"regression_passed", "regression_total"}
    measurement = spec.get("measurements")
    if not isinstance(measurement, dict) or set(measurement) != required_measurements:
        raise ValueError("measurements 必须声明回归测试分子与分母的证据位置")
    for rule in measurement.values():
        if not isinstance(rule, dict) or not isinstance(rule.get("artifact"), str) or not isinstance(rule.get("pointer"), str):
            raise ValueError("measurements 必须包含 artifact 和 pointer")
        relative = Path(rule["artifact"])
        if relative.is_absolute() or ".." in relative.parts or relative.name in {"run.json", "execution-manifest.json", "trace.jsonl"}:
            raise ValueError("measurement artifact 必须使用独立的相对证据路径")
    max_calls = spec.get("max_identical_tool_calls", 3)
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0:
        raise ValueError("max_identical_tool_calls 必须是正整数")
    variants = spec.get("variants")
    if not isinstance(variants, dict) or set(variants) != {"baseline", "candidate"}:
        raise ValueError("variants 必须包含 baseline 与 candidate")
    for name, variant in variants.items():
        if not isinstance(variant, dict) or not all(
                isinstance(variant.get(key), str) and variant[key].strip()
                for key in ("version", "configuration_ref")):
            raise ValueError(f"variants.{name} 必须冻结 version 和 configuration_ref")
    repeats = spec.get("repetitions")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 2:
        raise ValueError("repetitions 至少为 2，保留模型/工具波动的重复结果")
    seed = spec.get("order_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("必须在实验前固定整数 order_seed")
    budget = spec.get("budget")
    if not isinstance(budget, dict) or set(budget) != {"runtime_seconds", "tokens", "cost_yuan", "repair_attempts"}:
        raise ValueError("budget 必须固定 runtime_seconds、tokens、cost_yuan、repair_attempts")
    for name in ("runtime_seconds", "tokens", "repair_attempts"):
        value = budget[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"budget.{name} 必须是正整数")
    _finite_number(budget["cost_yuan"], "budget.cost_yuan", positive=True)
    thresholds = spec.get("thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) != THRESHOLDS:
        missing, extra = sorted(THRESHOLDS - set(thresholds or {})), sorted(set(thresholds or {}) - THRESHOLDS)
        raise ValueError(f"必须预先声明全部门槛；缺失={missing}，多余={extra}")
    for name in ("max_success_to_failure_pairs", "max_new_safety_violations", "max_new_contract_failures",
                 "max_new_regression_failures", "max_new_false_accepts", "max_new_false_pauses"):
        value = thresholds[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"thresholds.{name} 必须是非负整数")
    if thresholds["max_new_safety_violations"] != 0:
        raise ValueError("安全硬性失败不能设置非零容忍门槛")
    for name in ("min_repair_success_delta", "max_group_success_drop"):
        _finite_number(thresholds[name], f"thresholds.{name}")
        if thresholds[name] > 1:
            raise ValueError(f"thresholds.{name} 不能大于 1")
    for name in ("max_cost_ratio", "max_p95_latency_ratio"):
        _finite_number(thresholds[name], f"thresholds.{name}", positive=True)
    expected_groups = sorted(groups)
    protected = spec.get("protected_groups", expected_groups)
    if not isinstance(protected, list) or not protected or not set(protected) <= set(groups):
        raise ValueError("protected_groups 必须是冻结任务集中的非空分组子集")
    if spec.get("dataset_sha256") not in (None, dataset_hash):
        raise ValueError("实验注册中的 dataset_sha256 与当前冻结任务集不同")
    return {**spec, "max_identical_tool_calls": max_calls,
            "dataset_sha256": dataset_hash, "case_ids": [case["case_id"] for case in cases],
            "groups": groups, "protected_groups": sorted(protected)}


def register_experiment(spec_path: Path, dataset_path: Path, plan_path: Path,
                        contract_samples_path: Path | None = None) -> dict:
    _, cases, groups = _load_dataset(dataset_path)
    data_hash = file_hash(dataset_path)
    spec = _validate_spec(read_json(spec_path), cases, groups, data_hash)
    if spec["purpose"] in {"tool", "combined"} and contract_samples_path is None:
        raise ValueError("工具升级必须在注册前固定离线契约响应样本")
    if spec["purpose"] in {"tool", "combined"}:
        adapters = spec.get("contract_adapters")
        if not isinstance(adapters, dict) or set(adapters) != {"baseline", "candidate"}:
            raise ValueError("工具升级须预注册 baseline/candidate 离线适配器 module:function")
        spec["contract_adapters"] = {variant: adapter_identity(_adapter(target)) for variant, target in adapters.items()}
        sandbox_oracle = spec.get("sandbox_oracle")
        required_checks = set(HARD_OK) | {"target_ok", "side_effects_ok", "timeout_ok", "errors_ok"}
        if not isinstance(sandbox_oracle, dict) or set(sandbox_oracle) != required_checks:
            raise ValueError("工具升级必须预注册沙箱目标、权限、幂等、副作用、超时和错误码证据规则")
        for rules in sandbox_oracle.values():
            if not isinstance(rules, list) or not rules or any(not isinstance(rule, dict) or
                    not {"artifact", "pointer", "equals"} <= set(rule) for rule in rules):
                raise ValueError("sandbox_oracle 每项必须包含 artifact/pointer/equals 规则")
    if spec["purpose"] == "model" and contract_samples_path is not None:
        raise ValueError("纯模型升级不应附加工具契约样本")
    configuration_bytes, tool_contract_bytes = {}, {}
    for variant, entry in spec["variants"].items():
        config_path = (Path(spec_path).parent / entry["configuration_ref"]).resolve()
        config = read_json(config_path)
        entry["configuration_projection"] = _configuration(config)
        configuration_bytes[variant] = canonical(config)
        entry["configuration_sha256"] = sha256(configuration_bytes[variant])
        contract_ref = entry.get("tool_contract_ref")
        if config.get("tools", {}).get("mcp", {}).get("configuration", {}).get("servers") and not contract_ref:
            raise ValueError("含 MCP 服务的变体必须提供已发现的 tool_contract_ref 工具快照")
        if contract_ref:
            contract_path = (Path(spec_path).parent / contract_ref).resolve()
            tool_contract_bytes[variant] = canonical(read_json(contract_path))
            entry["tool_contract_sha256"] = sha256(tool_contract_bytes[variant])
    changed = {path for path in CONFIG_PATHS + OPTIONAL_CONFIG_PATHS if spec["variants"]["baseline"]["configuration_projection"][path]
               != spec["variants"]["candidate"]["configuration_projection"][path]}
    declared_changes = spec.get("allowed_changes")
    if spec["variants"]["baseline"].get("tool_contract_sha256") != spec["variants"]["candidate"].get("tool_contract_sha256"):
        changed.add("/discovered_tool_contract")
    if not isinstance(declared_changes, list) or not changed or changed != set(declared_changes):
        raise ValueError(f"allowed_changes 必须精确声明两变体的配置差异: {sorted(changed)}")
    plan_path = Path(plan_path)
    plan_path.mkdir(parents=True, exist_ok=False)
    write_once(plan_path / "benchmark.json", dataset_path.read_bytes())
    for variant, content in configuration_bytes.items():
        write_once(plan_path / f"config-{variant}.json", content)
    for variant, content in tool_contract_bytes.items():
        write_once(plan_path / f"tool-contract-{variant}.json", content)
    data = {**spec, "benchmark_file": "benchmark.json", "benchmark_sha256": data_hash,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "plan_version": 1, "schedule": _schedule(spec["case_ids"], spec["repetitions"], spec["order_seed"])}
    if contract_samples_path:
        contract_path = plan_path / "contract-samples.json"
        write_once(contract_path, contract_samples_path.read_bytes())
        data["contract_samples"] = {"path": contract_path.name, "sha256": file_hash(contract_path)}
    plan_bytes = canonical(data)
    write_once(plan_path / "plan.json", plan_bytes)
    write_once(plan_path / "plan.sha256", sha256(plan_bytes).encode("ascii"))
    return {"plan": data, "plan_sha256": sha256(plan_bytes), "path": str(plan_path)}


def _schedule(case_ids, repetitions, seed):
    rng = random.Random(seed)
    rows = []
    pairs = [(case_id, repeat) for case_id in case_ids for repeat in range(1, repetitions + 1)]
    rng.shuffle(pairs)
    for case_id, repeat in pairs:
        order = ["baseline", "candidate"]
        if rng.randrange(2):
            order.reverse()
        rows.append({"case_id": case_id, "repeat": repeat, "order": order})
    return rows


def _adapter(target):
    if not target:
        return None
    module_name, separator, function_name = target.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError("adapter 必须使用 package.module:function 格式")
    function = getattr(importlib.import_module(module_name), function_name, None)
    if not callable(function):
        raise ValueError("adapter 指向的对象不是可调用函数")
    return function


def adapter_identity(function):
    path = inspect.getsourcefile(function)
    if not path or not Path(path).is_file():
        raise ValueError("适配器必须来自可哈希的 Python 源文件")
    return {"name": f"{function.__module__}:{function.__name__}", "source_sha256": file_hash(Path(path))}


def check_contract_pair(samples_path, baseline, candidate):
    reports = {"baseline": check_contract_samples(samples_path, baseline),
               "candidate": check_contract_samples(samples_path, candidate)}
    for variant, adapter in (("baseline", baseline), ("candidate", candidate)):
        reports[variant]["adapter_identity"] = adapter_identity(adapter)
    before = {(row["contract"], row["case_id"]): row["passed"] for row in reports["baseline"]["cases"]}
    new_failures = [{"contract": row["contract"], "case_id": row["case_id"]}
                    for row in reports["candidate"]["cases"] if not row["passed"] and before[(row["contract"], row["case_id"])]]
    return {"kind": "paired_contract_samples", "samples_sha256": file_hash(samples_path),
            "baseline": reports["baseline"], "candidate": reports["candidate"],
            "new_failures": new_failures, "passed": reports["candidate"]["passed"]}


def check_contract_samples(samples_path: Path, adapter=None) -> dict:
    """Validate saved requests/responses and run a supplied offline adapter over each sample."""
    samples = read_json(samples_path)
    contracts = samples.get("contracts") if isinstance(samples, dict) else None
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("契约样本必须包含 contracts 列表")
    validator = adapter if callable(adapter) else None
    outcomes, errors = [], []
    for contract in contracts:
        name = contract.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("每个 contract 必须有 name")
        request_validator = Draft202012Validator(contract.get("request_schema", {}))
        response_validator = Draft202012Validator(contract.get("response_schema", {}))
        error_validator = Draft202012Validator(contract.get("error_schema", {"type": "object"}))
        for schema in (request_validator.schema, response_validator.schema, error_validator.schema):
            Draft202012Validator.check_schema(schema)
        if not contract.get("cases"):
            raise ValueError(f"{name} 缺少响应样本")
        candidate_validators = {}
        for part in ("request", "response", "error"):
            schema = contract.get(f"candidate_{part}_schema")
            if schema is not None:
                Draft202012Validator.check_schema(schema)
                candidate_validators[part] = Draft202012Validator(schema)
        for sample in contract.get("cases", []):
            case_id = sample.get("id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"{name} 的样本必须有 id")
            issues = []
            issues.extend(f"request:{_schema_issue(item)}" for item in request_validator.iter_errors(sample.get("request")))
            if "request" in candidate_validators:
                issues.extend(f"candidate_request:{_schema_issue(item)}" for item in candidate_validators["request"].iter_errors(sample.get("request")))
            upstream = sample.get("upstream")
            expected = sample.get("expected")
            if not isinstance(upstream, dict) or not isinstance(expected, dict):
                raise ValueError(f"{name}/{case_id} 缺少 upstream 或 expected")
            kind = expected.get("kind")
            if kind == "success":
                issues.extend(f"response:{_schema_issue(item)}" for item in response_validator.iter_errors(upstream.get("body")))
                if "response" in candidate_validators:
                    issues.extend(f"candidate_response:{_schema_issue(item)}" for item in candidate_validators["response"].iter_errors(upstream.get("body")))
            elif kind == "error":
                issues.extend(f"error:{_schema_issue(item)}" for item in error_validator.iter_errors(upstream.get("error")))
                if "error" in candidate_validators:
                    issues.extend(f"candidate_error:{_schema_issue(item)}" for item in candidate_validators["error"].iter_errors(upstream.get("error")))
                if upstream.get("error", {}).get("code") != expected.get("code"):
                    issues.append("error code differs from the frozen expectation")
            elif kind == "timeout":
                if isinstance(upstream.get("timeout_ms"), bool) or not isinstance(upstream.get("timeout_ms"), (int, float)):
                    issues.append("timeout sample must record timeout_ms")
                if not isinstance(expected.get("code"), str) or not expected["code"]:
                    issues.append("timeout expectation must record an error code")
            else:
                raise ValueError(f"{name}/{case_id} expected.kind 必须是 success/error/timeout")
            observed = None
            if not issues:
                try:
                    started = time.monotonic()
                    observed = validator(name, sample["request"], upstream) if validator else _default_adapter(upstream)
                    elapsed_ms = (time.monotonic() - started) * 1000
                    if "max_adapter_ms" in sample and elapsed_ms > sample["max_adapter_ms"]:
                        issues.append("adapter exceeded the frozen timeout expectation")
                except Exception as error:
                    observed = {"kind": "adapter_error", "type": type(error).__name__}
                if observed != expected:
                    issues.append("adapter result differs from the frozen expectation")
            outcomes.append({"contract": name, "case_id": case_id, "passed": not issues,
                             "expected": expected, "observed": observed, "issues": issues})
            if issues:
                errors.extend(f"{name}/{case_id}: {issue}" for issue in issues)
    return {"kind": "offline_contract_samples", "passed": not errors,
            "samples_sha256": file_hash(samples_path), "adapter": getattr(adapter, "__qualname__", "identity_fixture"),
            "failures_count": sum(not item["passed"] for item in outcomes),
            "adapter_supplied": validator is not None,
            "total": len(outcomes), "passed_count": sum(item["passed"] for item in outcomes),
            "failures": errors, "cases": outcomes}


def _default_adapter(upstream):
    if "timeout_ms" in upstream:
        return {"kind": "timeout", "code": upstream.get("error_code")}
    if isinstance(upstream.get("status"), int) and upstream["status"] >= 400:
        error = upstream.get("error") or {}
        return {"kind": "error", "code": error.get("code")}
    return {"kind": "success", "value": upstream.get("body")}


def _schema_issue(error):
    # Schema diagnostics must identify the field/rule without echoing credential values.
    path = "/" + "/".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.validator}"


def _trace_records(path, task_id, manifest_hash):
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Trace 缺失、损坏或含无效 JSON") from None
    if not records:
        raise ValueError("Trace 不能为空")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("Trace 事件必须为对象")
    if not any(item.get("manifest_hash") == manifest_hash for item in records):
        raise ValueError("Trace 没有与冻结配置清单关联的事件")
    if any(item.get("manifest_hash") not in (None, manifest_hash) for item in records):
        raise ValueError("Trace 混入其他配置版本的事件")
    if any(item.get("task_id") not in (None, task_id) for item in records):
        raise ValueError("Trace 混入其他任务的事件")
    return records


def _tool_contract(plan, variant, directory, events):
    expected = plan["variants"][variant].get("tool_contract_sha256")
    if not expected:
        return None
    used = any(event.get("operation") in {"tools.contract", "model.tool"} for event in events)
    if not used:
        return None
    snapshot = contained(directory, "mcp-tools.snapshot.json")
    if sha256(canonical(read_json(snapshot))) != expected:
        raise ValueError("实际发现的 MCP/本地工具 Schema 与预注册快照不一致")
    return expected


def _score_evidence(plan, raw, task_directory, trace_records, case_id):
    """Only the trusted test driver writes oracle evidence; the model's verdict is ignored."""
    evidence_files, evidence_values = {}, {}

    def observe(rule):
        name = rule["artifact"]
        path = contained(task_directory, name)
        if name not in evidence_values:
            evidence_values[name] = read_json(path)
            evidence_files[name] = file_hash(path)
        return pointer(evidence_values[name], rule["pointer"])

    assessment, metrics = {}, {}
    for metric, rules in plan["oracle"].items():
        # Inspect every artifact even if the first assertion already failed.
        passed = all([canonical(observe(rule)) == canonical(rule["equals"]) for rule in rules])
        assessment[metric] = {"passed": passed, "evidence": rules}
        metrics[metric] = passed
    metrics["safety_violation"] = not metrics.pop("safety_ok")
    terminals = [item for item in trace_records if item.get("operation") == "workflow"
                 and item.get("event") in {"finished", "paused"}]
    if not terminals:
        raise ValueError("Trace 缺少工作流终止事件，不能从部分过程推断最终效果")
    terminal = terminals[-1]
    status = (terminal.get("result") or {}).get("status")
    if not isinstance(status, str):
        raise ValueError("工作流终止事件缺少状态")
    metrics["false_accept"] = status in {"reviewable", "succeeded"} and not metrics["repair_success"]
    metrics["false_pause"] = status == "needs_input" and case_id not in plan["expected_pause_cases"]
    metrics["terminal_reason"] = (terminal.get("details") or {}).get("reason")
    for name, rule in plan["measurements"].items():
        metrics[name] = observe(rule)
    accounting = (terminal.get("result") or {}).get("accounting") or {}
    metrics["cost_yuan"] = accounting.get("cost_observed_yuan") if accounting.get("cost_complete") is True else None
    # End-to-end latency includes SDK/worker overhead, ending before human review.
    try:
        started = datetime.fromisoformat(trace_records[0]["timestamp"])
        ended = datetime.fromisoformat(terminal["timestamp"])
        metrics["latency_seconds"] = (ended - started).total_seconds()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Trace 缺少有效时间戳，无法计算端到端耗时") from None
    counts = {}
    tools_seen, successful_tools, parameters_ok = set(), set(), True
    for event in trace_records:
        if event.get("operation") == "model.tool" and event.get("event") == "started":
            signature = (event.get("tool_name"), sha256(canonical(event.get("input"))))
            counts[signature] = counts.get(signature, 0) + 1
            name = event.get("tool_name")
            tools_seen.add(name)
            schema = plan["trace_policy"]["parameter_schemas"].get(name)
            if schema is None or not Draft202012Validator(schema).is_valid(event.get("input")):
                parameters_ok = False
        if event.get("operation") == "model.tool" and event.get("event") == "succeeded":
            successful_tools.add(event.get("tool_name"))
    nodes_seen = {event.get("details", {}).get("node") for event in trace_records
                  if event.get("operation") == "checkpoint" and event.get("event") == "committed"}
    metrics.update(tool_selection_ok=tools_seen <= set(plan["trace_policy"]["allowed_tools"]),
                   tool_parameters_ok=parameters_ok,
                   required_steps_ok=set(plan["trace_policy"]["required_tools"]) <= successful_tools
                   and set(plan["trace_policy"]["required_nodes"]) <= nodes_seen)
    max_same_call = plan.get("max_identical_tool_calls", 3)
    metrics["loop_detected"] = any(count > max_same_call for count in counts.values())
    return metrics, assessment, evidence_files


def ingest_run(plan_path: Path, variant: str, case_id: str, repeat: int,
               result_path: Path, task_directory: Path) -> dict:
    plan = load_plan(plan_path)
    if variant not in {"baseline", "candidate"} or variant not in plan["variants"]:
        raise ValueError("variant 必须是已注册的 baseline 或 candidate")
    if case_id not in plan["case_ids"] or isinstance(repeat, bool) or not 1 <= repeat <= plan["repetitions"]:
        raise ValueError("case_id/repeat 不在预注册的任务范围内")
    task_directory = Path(task_directory)
    manifest = read_json(task_directory / "execution-manifest.json")
    manifest_hash = sha256(canonical(manifest))
    trace_path = task_directory / "trace.jsonl"
    task_id = manifest.get("task", {}).get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("冻结清单缺少 task.task_id")
    trace_records = _trace_records(trace_path, task_id, manifest_hash)
    try:
        if datetime.fromisoformat(trace_records[0]["timestamp"]) < datetime.fromisoformat(plan["registered_at"]):
            raise ValueError("本次执行早于实验预注册；必须在固定门槛后重新配对运行")
    except (KeyError, TypeError):
        raise ValueError("Trace 缺少可核对的运行开始时间") from None
    tool_contract_hash = _tool_contract(plan, variant, task_directory, trace_records)
    dataset = read_json(Path(plan_path) / "benchmark.json")
    registered_case = next(case for case in dataset["cases"] if case["case_id"] == case_id)
    if "query" in registered_case and manifest.get("task", {}).get("initial_state", {}).get("original_report") != registered_case["query"]:
        raise ValueError("本次任务缺陷描述与冻结题目不一致")
    if "input" in registered_case and manifest.get("task", {}).get("input_sha256") != sha256(canonical(registered_case["input"])):
        raise ValueError("本次任务输入与冻结题目不一致")
    if _configuration(manifest) != plan["variants"][variant]["configuration_projection"]:
        raise ValueError("本次执行配置与预注册变体不一致")
    budget = manifest.get("task", {}).get("budget", {})
    expected_budget = plan["budget"]
    if budget != {"seconds": expected_budget["runtime_seconds"], "tokens": expected_budget["tokens"],
                  "cost": expected_budget["cost_yuan"], "attempts": expected_budget["repair_attempts"]}:
        raise ValueError("本次任务初始预算与预注册预算不一致")
    raw = read_json(result_path)
    if raw.get("evidence_kind") != plan["evidence_kind"]:
        raise ValueError("结果的实测/合成来源必须与预注册一致")
    metrics, assessment, evidence_files = _score_evidence(plan, raw, task_directory, trace_records, case_id)
    for name in RUN_METRICS:
        if not isinstance(metrics.get(name), bool):
            raise ValueError(f"metrics.{name} 必须是独立验收后的布尔结果")
    _finite_number(metrics.get("cost_yuan"), "metrics.cost_yuan", allow_none=True)
    _finite_number(metrics.get("latency_seconds"), "metrics.latency_seconds", positive=True)
    if not isinstance(metrics.get("terminal_reason"), str) or not metrics["terminal_reason"]:
        raise ValueError("metrics.terminal_reason 必须记录终止原因")
    for name in ("regression_passed", "regression_total"):
        value = metrics.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"metrics.{name} 必须是非负整数")
    if metrics["regression_passed"] > metrics["regression_total"] or metrics["regression_total"] == 0:
        raise ValueError("必须提供非空且有效的回归测试分子/分母")
    if metrics["regression_pass"] != (metrics["regression_passed"] == metrics["regression_total"]):
        raise ValueError("regression_pass 与实际回归用例计数不一致")
    expected_dir = Path(plan_path) / "runs" / variant / case_id / f"repeat-{repeat:02d}"
    expected_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(task_directory / "execution-manifest.json", expected_dir / "execution-manifest.json")
    shutil.copyfile(trace_path, expected_dir / "trace.jsonl")
    if tool_contract_hash:
        shutil.copyfile(contained(task_directory, "mcp-tools.snapshot.json"), expected_dir / "mcp-tools.snapshot.json")
    for name in evidence_files:
        destination = expected_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contained(task_directory, name), destination)
    record = {"experiment_id": plan["experiment_id"], "variant": variant, "case_id": case_id,
              "repeat": repeat, "task_id": task_id, "manifest_sha256": manifest_hash,
              "trace_sha256": file_hash(trace_path), "trace_events": len(trace_records),
              "metrics": metrics, "assessment": assessment, "evidence_files": evidence_files,
              "input_sha256": manifest.get("task", {}).get("input_sha256"),
              "source_fingerprint": manifest.get("task", {}).get("source_fingerprint"),
              "evidence_kind": raw["evidence_kind"]}
    record["tool_contract_sha256"] = tool_contract_hash
    write_once(expected_dir / "run.json", canonical(record))
    return record


def _percentile95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _load_runs(plan_path, plan):
    expected = {(variant, case_id, repeat)
                for variant in ("baseline", "candidate") for case_id in plan["case_ids"]
                for repeat in range(1, plan["repetitions"] + 1)}
    actual, rows, task_ids, case_inputs = set(), {}, set(), {}
    for path in (Path(plan_path) / "runs").glob("*/**/run.json"):
        record = read_json(path)
        identity = (record.get("variant"), record.get("case_id"), record.get("repeat"))
        if identity not in expected:
            raise ValueError(f"发现未注册的运行结果: {identity}")
        if identity in actual:
            raise ValueError(f"重复运行结果: {identity}")
        manifest_path = path.parent / "execution-manifest.json"
        trace_path = path.parent / "trace.jsonl"
        manifest = read_json(manifest_path)
        manifest_hash = sha256(canonical(manifest))
        if manifest_hash != record.get("manifest_sha256"):
            raise ValueError(f"{identity} 冻结清单哈希不一致")
        trace = _trace_records(trace_path, record.get("task_id"), manifest_hash)
        if _tool_contract(plan, identity[0], path.parent, trace) != record.get("tool_contract_sha256"):
            raise ValueError("工具契约索引与实际快照不一致")
        if file_hash(trace_path) != record.get("trace_sha256") or len(trace) != record.get("trace_events"):
            raise ValueError(f"{identity} Trace 证据与运行索引不一致")
        if _configuration(manifest) != plan["variants"][identity[0]]["configuration_projection"]:
            raise ValueError(f"{identity} 执行配置与预注册变体不一致")
        if record.get("evidence_kind") != plan["evidence_kind"]:
            raise ValueError("合成结果不能混入真实模型实验")
        for name, identity_hash in record.get("evidence_files", {}).items():
            if file_hash(contained(path.parent, name)) != identity_hash:
                raise ValueError(f"{identity} 验收证据已变化: {name}")
        metrics, assessment, evidence_files = _score_evidence(plan, record, path.parent, trace, identity[1])
        if (metrics != record.get("metrics") or assessment != record.get("assessment")
                or evidence_files != record.get("evidence_files")):
            raise ValueError(f"{identity} 结果与独立证据重算不一致")
        if record.get("task_id") in task_ids:
            raise ValueError("不同重复实验不得复用同一个任务执行")
        task_ids.add(record.get("task_id"))
        task_input = manifest.get("task", {}).get("input_sha256")
        if not isinstance(task_input, str) or not task_input:
            raise ValueError("缺少冻结任务输入哈希")
        source = manifest.get("task", {}).get("source_fingerprint")
        pair_input = {"input_sha256": task_input, "source_fingerprint": source,
                      "budget": manifest.get("task", {}).get("budget"),
                      "memory": manifest.get("task", {}).get("memory")}
        if identity[1] in case_inputs and case_inputs[identity[1]] != pair_input:
            raise ValueError(f"{identity[1]} 的输入、源码、记忆或预算在配对重复运行间发生变化")
        case_inputs[identity[1]] = pair_input
        actual.add(identity)
        rows[identity] = record
    missing = sorted(expected - actual)
    if missing:
        raise ValueError(f"运行结果不完整，缺少 {len(missing)} 个配对项；示例: {missing[:4]}")
    return rows


def _contract_counts(contract_report, plan_path, plan):
    if plan["purpose"] == "model":
        return 0
    if not isinstance(contract_report, dict):
        raise ValueError("工具升级必须提供离线契约样本报告")
    pinned = plan.get("contract_samples", {})
    sample_copy = Path(plan_path) / pinned.get("path", "")
    if file_hash(sample_copy) != pinned.get("sha256") or contract_report.get("samples_sha256") != pinned.get("sha256"):
        raise ValueError("离线契约报告与预注册的响应样本不匹配")
    if contract_report.get("kind") != "paired_contract_samples":
        raise ValueError("工具升级必须提供旧/新适配器配对离线报告")
    for variant in ("baseline", "candidate"):
        report = contract_report.get(variant, {})
        if not report.get("adapter_supplied") or not report.get("cases"):
            raise ValueError("仅检查样本自身不足以验证适配器；必须分别驱动旧/新适配逻辑")
        if report.get("samples_sha256") != pinned["sha256"]:
            raise ValueError("旧/新适配器必须使用同一冻结响应样本")
        if report.get("adapter_identity") != plan["contract_adapters"][variant]:
            raise ValueError("离线报告的适配器身份与预注册不一致")
    # Existing adapter failures are reported too; no known broken contract is releasable.
    return sum(row.get("passed") is not True for row in contract_report["candidate"]["cases"])


def _validate_sandbox_report(report, plan, directory=None):
    if plan["purpose"] == "model":
        return
    if not isinstance(report, dict) or report.get("experiment_id") != plan["experiment_id"]:
        raise ValueError("工具升级必须提供绑定本实验的沙箱接口报告")
    if report.get("environment") != "sandbox" or report.get("status") != "passed":
        raise ValueError("真实接口行为必须在 sandbox 环境通过；真实生产写操作不属于此门禁")
    if not isinstance(report.get("evidence"), list) or not report["evidence"]:
        raise ValueError("沙箱报告必须引用可核对的请求/副作用证据")
    if report.get("evidence_kind") != plan["evidence_kind"]:
        raise ValueError("沙箱报告来源必须与预注册的 synthetic/live 一致")
    hashes = {}
    for ref in report["evidence"]:
        if not isinstance(ref, dict) or not {"path", "sha256"} <= set(ref):
            raise ValueError("沙箱证据必须固定 path/sha256")
        path = contained(directory, ref["path"])
        if file_hash(path) != ref["sha256"]:
            raise ValueError("沙箱副作用证据哈希已变化")
        hashes[ref["path"]] = ref["sha256"]
    for metric, rules in plan["sandbox_oracle"].items():
        for rule in rules:
            if rule["artifact"] not in hashes:
                raise ValueError("沙箱证据未列入报告索引")
            value = pointer(read_json(contained(directory, rule["artifact"])), rule["pointer"])
            if canonical(value) != canonical(rule["equals"]):
                raise ValueError(f"沙箱真实接口行为证据未满足规则: {metric}")


def evaluate_upgrade(plan_path: Path, *, contract_report_path: Path | None = None,
                     sandbox_report_path: Path | None = None) -> dict:
    plan_path = Path(plan_path)
    plan_bytes = (plan_path / "plan.json").read_bytes()
    plan = load_plan(plan_path)
    if sha256((plan_path / "benchmark.json").read_bytes()) != plan["benchmark_sha256"]:
        raise ValueError("实验内冻结任务集哈希已改变")
    _, cases, groups = _load_dataset(plan_path / "benchmark.json")
    if {key: sorted(value) for key, value in groups.items()} != {
            key: sorted(value) for key, value in plan["groups"].items()}:
        raise ValueError("冻结任务集分组与注册清单不一致")
    contract_report = read_json(contract_report_path) if contract_report_path else None
    contract_failures = _contract_counts(contract_report, plan_path, plan)
    sandbox_report = read_json(sandbox_report_path) if sandbox_report_path else None
    _validate_sandbox_report(sandbox_report, plan, sandbox_report_path.parent if sandbox_report_path else None)
    rows = _load_runs(plan_path, plan)

    def values(variant, metric):
        return [rows[(variant, case_id, repeat)]["metrics"][metric]
                for case_id in plan["case_ids"] for repeat in range(1, plan["repetitions"] + 1)]

    baseline_success = values("baseline", "repair_success")
    candidate_success = values("candidate", "repair_success")
    success_to_failure = []
    new_safety, new_regressions, new_false_accepts, new_false_pauses = [], [], [], []
    new_behavior = {name: [] for name in HARD_OK}
    for case_id in plan["case_ids"]:
        for repeat in range(1, plan["repetitions"] + 1):
            before = rows[("baseline", case_id, repeat)]["metrics"]
            after = rows[("candidate", case_id, repeat)]["metrics"]
            pair = {"case_id": case_id, "repeat": repeat}
            if before["repair_success"] and not after["repair_success"]:
                success_to_failure.append(pair)
            for metric, target in (("safety_violation", new_safety),):
                if after[metric] and not before[metric]:
                    target.append({**pair, "metric": metric})
            for metric, target in (("regression_pass", new_regressions),
                                   ("false_accept", new_false_accepts), ("false_pause", new_false_pauses)):
                regressed = (before[metric] and not after[metric]) if metric == "regression_pass" else (after[metric] and not before[metric])
                if regressed:
                    target.append({**pair, "metric": metric})
            for metric in HARD_OK:
                if before[metric] and not after[metric]:
                    new_behavior[metric].append(pair)

    group_report = {}
    for group in groups:
        ids = groups[group]
        before = [rows[("baseline", case_id, repeat)]["metrics"]["repair_success"]
                  for case_id in ids for repeat in range(1, plan["repetitions"] + 1)]
        after = [rows[("candidate", case_id, repeat)]["metrics"]["repair_success"]
                 for case_id in ids for repeat in range(1, plan["repetitions"] + 1)]
        group_report[group] = {"baseline_rate": sum(before) / len(before), "candidate_rate": sum(after) / len(after),
                               "drop": (sum(before) - sum(after)) / len(before)}
    baseline_cost, candidate_cost = values("baseline", "cost_yuan"), values("candidate", "cost_yuan")
    if any(value is None for value in baseline_cost + candidate_cost):
        raise ValueError("有运行未报告实际费用；未知费用不能通关")
    baseline_latency, candidate_latency = values("baseline", "latency_seconds"), values("candidate", "latency_seconds")
    if any(value <= 0 for value in baseline_latency + candidate_latency):
        raise ValueError("延迟必须是正数")
    cost_ratio = statistics.mean(candidate_cost) / max(statistics.mean(baseline_cost), 1e-12)
    p95_before, p95_after = _percentile95(baseline_latency), _percentile95(candidate_latency)
    latency_ratio = p95_after / p95_before
    threshold = plan["thresholds"]
    delta = sum(candidate_success) / len(candidate_success) - sum(baseline_success) / len(baseline_success)
    group_drops = {name: item["drop"] for name, item in group_report.items()
                   if name in plan["protected_groups"] and item["drop"] > threshold["max_group_success_drop"]}
    critical_case_regressions = []
    for group in plan["protected_groups"]:
        for case_id in groups[group]:
            before = sum(rows[("baseline", case_id, repeat)]["metrics"]["repair_success"] for repeat in range(1, plan["repetitions"] + 1))
            after = sum(rows[("candidate", case_id, repeat)]["metrics"]["repair_success"] for repeat in range(1, plan["repetitions"] + 1))
            if before > after:
                critical_case_regressions.append({"case_id": case_id, "group": group,
                                                  "baseline_successes": before, "candidate_successes": after})
    checks = {
        "minimum_repair_success_delta": delta >= threshold["min_repair_success_delta"],
        "success_to_failure_pairs": len(success_to_failure) <= threshold["max_success_to_failure_pairs"],
        "new_safety_violations": len(new_safety) <= threshold["max_new_safety_violations"],
        "contract_samples": contract_failures <= threshold["max_new_contract_failures"],
        "regression_failures": len(new_regressions) <= threshold["max_new_regression_failures"],
        "false_accepts": len(new_false_accepts) <= threshold["max_new_false_accepts"],
        "false_pauses": len(new_false_pauses) <= threshold["max_new_false_pauses"],
        "protected_groups": not group_drops,
        "critical_cases": not critical_case_regressions,
        "cost_ratio": cost_ratio <= threshold["max_cost_ratio"],
        "p95_latency_ratio": latency_ratio <= threshold["max_p95_latency_ratio"],
    }
    for name, rows_failed in new_behavior.items():
        checks[f"no_new_{name}"] = not rows_failed
    # A known safety problem is a hard failure even if the baseline had it too.
    checks["candidate_safety"] = not any(values("candidate", "safety_violation"))
    for name in HARD_OK:
        checks[f"candidate_{name}"] = all(values("candidate", name))
    for name in ("tool_selection_ok", "tool_parameters_ok", "required_steps_ok"):
        checks[f"no_new_{name}"] = not any(
            rows[("baseline", case_id, repeat)]["metrics"][name] and not rows[("candidate", case_id, repeat)]["metrics"][name]
            for case_id in plan["case_ids"] for repeat in range(1, plan["repetitions"] + 1))
    checks["no_new_loops"] = not any(not before and after for before, after in zip(
        values("baseline", "loop_detected"), values("candidate", "loop_detected")))
    failures = [name for name, passed in checks.items() if not passed]
    summaries = {}
    for variant in ("baseline", "candidate"):
        costs = values(variant, "cost_yuan")
        regression_total = sum(values(variant, "regression_total"))
        summaries[variant] = {
            **{name: {"true": sum(values(variant, name)), "total": len(baseline_success),
                      "rate": sum(values(variant, name)) / len(baseline_success)} for name in RUN_METRICS},
            "regression_passed": sum(values(variant, "regression_passed")),
            "regression_total": regression_total,
            "regression_pass_rate": sum(values(variant, "regression_passed")) / regression_total,
            "mean_cost_yuan": statistics.mean(costs), "total_cost_yuan": sum(costs),
            "cost_per_success_yuan": sum(costs) / sum(values(variant, "repair_success")) if any(values(variant, "repair_success")) else None,
            "p95_latency_seconds": _percentile95(values(variant, "latency_seconds"))}
    detail = {"experiment_id": plan["experiment_id"], "purpose": plan["purpose"],
        "plan_sha256": sha256(plan_bytes), "dataset_sha256": plan["benchmark_sha256"],
        "variants": plan["variants"], "repetitions": plan["repetitions"],
        "runs_per_variant": len(plan["case_ids"]) * plan["repetitions"],
        "summary": summaries,
        "metrics": {"baseline_repair_success_rate": sum(baseline_success) / len(baseline_success),
                    "candidate_repair_success_rate": sum(candidate_success) / len(candidate_success),
                    "repair_success_delta": delta, "success_to_failure": success_to_failure,
                    "new_safety_violations": new_safety, "new_behavior_failures": new_behavior,
                    "new_regression_failures": new_regressions, "new_false_accepts": new_false_accepts,
                    "new_false_pauses": new_false_pauses, "contract_failures": contract_failures,
                    "groups": group_report, "group_drops_over_limit": group_drops,
                    "critical_case_regressions": critical_case_regressions,
                    "mean_cost_ratio": cost_ratio, "baseline_p95_latency_seconds": p95_before,
                    "candidate_p95_latency_seconds": p95_after, "p95_latency_ratio": latency_ratio},
        "checks": checks, "passed": not failures, "failed_checks": failures,
        "release_eligible": not failures and plan["evidence_kind"] == "live",
        "per_case": [{"case_id": case_id, "baseline_successes": sum(rows[("baseline", case_id, repeat)]["metrics"]["repair_success"]
                         for repeat in range(1, plan["repetitions"] + 1)),
                      "candidate_successes": sum(rows[("candidate", case_id, repeat)]["metrics"]["repair_success"]
                         for repeat in range(1, plan["repetitions"] + 1)), "repetitions": plan["repetitions"]}
                     for case_id in plan["case_ids"]],
        "provenance": {"run_results": plan["evidence_kind"], "contract":
            "offline_samples" if plan["purpose"] != "model" else "not_applicable",
            "sandbox": "passed_evidence" if plan["purpose"] != "model" else "not_applicable"}}
    report_path = plan_path / "report.json"
    write_once(report_path, canonical(detail))
    return detail


def run_pairs(plan_path: Path, runner) -> dict:
    """Drive a trusted integration runner in the registered order, keeping every failed run."""
    plan = load_plan(plan_path)
    cases = {row["case_id"]: row for row in read_json(Path(plan_path) / "benchmark.json")["cases"]}
    completed = []
    for pair in plan["schedule"]:
        for variant in pair["order"]:
            slot = Path(plan_path) / "runs" / variant / pair["case_id"] / f"repeat-{pair['repeat']:02d}"
            if slot.exists():
                raise ValueError("存在已运行的实验槽位；禁止覆盖或挑选重复结果，请新建实验或继续导入未执行项")
            request = {"case": deepcopy(cases[pair["case_id"]]), "repeat": pair["repeat"],
                       "variant": variant, "budget": plan["budget"],
                       "configuration": read_json(Path(plan_path) / f"config-{variant}.json"),
                       "idempotency_key": sha256(canonical([file_hash(Path(plan_path) / "plan.json"),
                                                           pair["case_id"], pair["repeat"], variant])),
                       "evidence_kind": plan["evidence_kind"]}
            dispatch_path = Path(plan_path) / "dispatch" / variant / pair["case_id"] / f"repeat-{pair['repeat']:02d}.json"
            write_once(dispatch_path, canonical({"idempotency_key": request["idempotency_key"],
                       "variant": variant, "case_id": pair["case_id"], "repeat": pair["repeat"],
                       "dispatched_at": datetime.now(timezone.utc).isoformat(),
                       "runner": adapter_identity(runner)}))
            try:
                result = runner(request)
            except Exception as error:
                write_once(dispatch_path.with_suffix(".failure.json"), canonical({"error_type": type(error).__name__,
                    "outcome": "runner_failed", "requires_evidence_reconciliation": True}))
                raise ValueError("评测驱动异常，已保留运行意图；核对原结果后导入，不自动重跑该槽位") from None
            if not isinstance(result, dict) or not {"result_path", "task_directory"} <= set(result):
                raise ValueError("runner 必须返回 result_path 与 task_directory；正常失败也应导出证据")
            completed.append(ingest_run(plan_path, variant, pair["case_id"], pair["repeat"],
                                        Path(result["result_path"]), Path(result["task_directory"])))
    return {"completed_runs": len(completed), "evidence_kind": plan["evidence_kind"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="预注册模型/工具升级配对评测门禁")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--spec", type=Path, required=True)
    register.add_argument("--dataset", type=Path, default=ROOT / "benchmark.json")
    register.add_argument("--plan", type=Path, required=True)
    register.add_argument("--contract-samples", type=Path)
    samples = commands.add_parser("check-contract")
    samples.add_argument("--samples", type=Path, required=True)
    samples.add_argument("--adapter")
    samples.add_argument("--baseline-adapter")
    samples.add_argument("--output", type=Path)
    ingest = commands.add_parser("ingest-run")
    ingest.add_argument("--plan", type=Path, required=True)
    ingest.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    ingest.add_argument("--case", required=True)
    ingest.add_argument("--repeat", type=int, required=True)
    ingest.add_argument("--result", type=Path, required=True)
    ingest.add_argument("--task-dir", type=Path, required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--plan", type=Path, required=True)
    evaluate.add_argument("--contract-report", type=Path)
    evaluate.add_argument("--sandbox-report", type=Path)
    run = commands.add_parser("run-pairs")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--runner", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            result = register_experiment(args.spec, args.dataset, args.plan, args.contract_samples)
        elif args.command == "check-contract":
            if args.baseline_adapter:
                if not args.adapter:
                    raise ValueError("配对契约评测同时需要 --baseline-adapter 与 --adapter")
                result = check_contract_pair(args.samples, _adapter(args.baseline_adapter), _adapter(args.adapter))
            else:
                result = check_contract_samples(args.samples, _adapter(args.adapter))
            if args.output:
                write_once(args.output, canonical(result))
        elif args.command == "ingest-run":
            result = ingest_run(args.plan, args.variant, args.case, args.repeat, args.result, args.task_dir)
        elif args.command == "run-pairs":
            result = run_pairs(args.plan, _adapter(args.runner))
        else:
            result = evaluate_upgrade(args.plan, contract_report_path=args.contract_report,
                                      sandbox_report_path=args.sandbox_report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("passed", True) else 1
    except (ValueError, OSError, SchemaError, ImportError, AttributeError) as error:
        print(f"upgrade gate error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
