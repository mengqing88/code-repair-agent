"""Strict model contracts; syntax/schema corrections are distinct from transport retries."""
from __future__ import annotations

import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from resilience import Fault, current, output_schema, trace, error_info


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReproductionPlan(Contract):
    revised_report: str
    steps: str
    reproduction_code: str
    question: str


class ReproductionComparison(Contract):
    comparison: Literal["matched", "mismatch", "inconclusive"]
    conclusion: str
    evidence: str
    question: str


class RegressionPlan(Contract):
    test_body: str
    question: str


class CaseAssessment(Contract):
    case_id: str
    revision: int
    decision: Literal["applicable", "rejected", "inconclusive"]
    matched_conditions: list[str]
    conflicts: list[str]
    unknowns: list[str]


class PatchProposal(Contract):
    diagnosis: str = Field(min_length=1)
    patch_diff: str = Field(min_length=1)


class RagPatchProposal(PatchProposal):
    case_assessments: list[CaseAssessment]


class ReadArguments(Contract):
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def path_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path 不能为空白字符串")
        return value


class SearchArguments(Contract):
    query: str = Field(min_length=1, max_length=2000)
    path: str = "."
    max_results: int = Field(default=50, ge=1, le=500)

    @field_validator("query", "path")
    @classmethod
    def strings_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("文本参数不能为空白字符串")
        return value


class RetrieveArguments(Contract):
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空白字符串")
        return value


class ApplyPatchArguments(Contract):
    patch_diff: str = Field(min_length=1, max_length=200_000)

    @field_validator("patch_diff")
    @classmethod
    def patch_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("补丁内容不能为空白字符串")
        return value


class RunTestsArguments(Contract):
    pass


TOOL_ARGUMENTS = {
    "read_code": ReadArguments,
    "search_code": SearchArguments,
    "retrieve_repair_experience": RetrieveArguments,
    "apply_patch": ApplyPatchArguments,
    "run_tests": RunTestsArguments,
}

# Server policy allows only the declared, validated tools; memory writes stay in fixed workflow nodes.
MODEL_TOOL_ALLOWLIST = frozenset(TOOL_ARGUMENTS)


def parse_object(text):
    if not isinstance(text, str):
        raise ValueError("回复必须是字符串")
    text = text.strip()
    match = re.fullmatch(r"(?P<fence>`{3,})(?:json)?\r?\n(?P<body>.*?)\r?\n(?P=fence)", text, re.DOTALL)
    if match:
        text = match.group("body")
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("JSON 对象包含重复字段")
            result[k] = v
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON 不允许 NaN 或 Infinity")))
    except json.JSONDecodeError as error:
        raise ValueError(f"未返回有效 JSON：第 {error.lineno} 行，第 {error.colno} 列") from None
    if not isinstance(value, dict):
        raise ValueError("模型回复必须是 JSON 对象")
    return value


def generate_object(prompt, call, contract, *, validate=None, max_corrections=2):
    original = prompt
    tokens = cost = 0
    input_tokens = output_tokens = 0
    cost_known = True
    for correction in range(max_corrections + 1):
        if current():
            current().check()
        with output_schema({"name": contract.__name__, "schema": contract.model_json_schema()}):
            response = call(prompt)
        text = response if isinstance(response, str) else getattr(response, "text", None)
        tokens += getattr(response, "total_tokens", 0)
        input_tokens += getattr(response, "input_tokens", 0)
        output_tokens += getattr(response, "output_tokens", 0)
        charge = getattr(response, "cost_yuan", None)
        cost_known = cost_known and charge is not None
        cost += charge or 0
        try:
            data = contract.model_validate(parse_object(text)).model_dump()
            if validate:
                validate(data)
        except (ValueError, ValidationError, SyntaxError) as error:
            rejected = Fault("schema_invalid", "模型输出未通过校验", category="validation",
                             causes=[error_info(error, operation="model.output.validate")])
            if isinstance(error, ValidationError):
                issues = [{"path": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                          for item in error.errors(include_input=False, include_url=False)]
            else:
                issues = [{"type": type(error).__name__, "message": str(error)[:500]}]
            trace("model.output", "schema_rejected", parameters={"contract": contract.__name__},
                  attempt=correction, fault=rejected, decision="regenerate" if correction < max_corrections else "stop")
            if correction == max_corrections:
                raise Fault("schema_invalid", "模型输出未通过校验，纠错次数已用尽", category="validation",
                            next_action="检查输入与输出契约，补充信息后重试", causes=[error_info(rejected)]) from None
            # Error fields do not echo invalid raw values; original task remains the source of truth.
            prompt = original + "\n服务端校验未通过。保持原任务不变，修正以下错误后重新返回完整 JSON：\n" + json.dumps(issues, ensure_ascii=False)
            continue
        return data, {
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": tokens,
            },
            **({"estimated_cost": cost} if cost_known else {}),
        }
