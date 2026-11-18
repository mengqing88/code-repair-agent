"""Strict model contracts; syntax/schema corrections are distinct from transport retries."""
from __future__ import annotations

import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from code_repair.resilience import Fault, current, output_schema, trace, error_info


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClarifiableContract(Contract):
    clarification_required: bool
    clarification_reasons: list[str]
    question: str

    @model_validator(mode="after")
    def validate_clarification(self):
        reasons = [reason.strip() for reason in self.clarification_reasons]
        if self.clarification_required:
            if not self.question.strip() or not reasons or any(not reason for reason in reasons):
                raise ValueError("需要澄清时必须提供问题和具体未知项")
        elif self.question.strip() or reasons:
            raise ValueError("信息充分时 question 和 clarification_reasons 必须为空")
        return self


class ReproductionPlan(ClarifiableContract):
    revised_report: str
    steps: str
    reproduction_code: str

    @model_validator(mode="after")
    def validate_plan(self):
        if self.clarification_required == bool(self.reproduction_code.strip()):
            raise ValueError("需要澄清时复现脚本必须为空；否则必须提供脚本")
        return self


class FixedCommandReproductionPlan(ClarifiableContract):
    revised_report: str
    steps: str
    reproduction_code: str

    @model_validator(mode="after")
    def validate_fixed_command_plan(self):
        if self.reproduction_code.strip():
            raise ValueError("固定复现命令或非 Python 代码不能生成 Python 复现脚本")
        return self


class ReproductionComparison(ClarifiableContract):
    comparison: Literal["matched", "mismatch", "inconclusive"]
    conclusion: str
    evidence: str

    @model_validator(mode="after")
    def validate_comparison(self):
        if (self.comparison == "matched") == self.clarification_required:
            raise ValueError("仅无法确认或与报告不符时需要澄清")
        return self


class RegressionPlan(ClarifiableContract):
    test_body: str

    @model_validator(mode="after")
    def validate_test_body(self):
        if self.clarification_required == bool(self.test_body.strip()):
            raise ValueError("需要澄清时 test_body 必须为空；否则必须提供测试")
        return self


class CaseAssessment(Contract):
    case_id: str
    revision: int
    decision: Literal["applicable", "rejected", "inconclusive"]
    matched_conditions: list[str]
    conflicts: list[str]
    unknowns: list[str]


class PatchProposal(ClarifiableContract):
    diagnosis: str
    patch_diff: str

    @model_validator(mode="after")
    def validate_proposal(self):
        if self.clarification_required == bool(self.diagnosis.strip() or self.patch_diff.strip()):
            raise ValueError("需要澄清时诊断和补丁必须为空；否则两者都必须提供")
        if not self.clarification_required and (not self.diagnosis.strip() or not self.patch_diff.strip()):
            raise ValueError("信息充分时诊断和补丁都必须非空")
        return self


class RagPatchProposal(PatchProposal):
    case_assessments: list[CaseAssessment]


class ReadArguments(Contract):
    path: str = Field(min_length=1, max_length=4096)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def path_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("path 不能为空白字符串")
        return value

    @model_validator(mode="after")
    def validate_line_range(self):
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("start_line 和 end_line 必须同时提供")
        if self.start_line is not None and not 0 <= self.end_line - self.start_line <= 299:
            raise ValueError("行范围必须有效，单次 read_code 最多读取 300 行")
        return self


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
    outcome: Literal["accepted", "rejected"] = "accepted"
    file_paths: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空白字符串")
        return value

    @field_validator("file_paths")
    @classmethod
    def file_paths_must_be_relative(cls, value: list[str]) -> list[str]:
        for path in value:
            normalized = path.replace("\\", "/")
            if (not path.strip() or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized)
                    or ".." in normalized.split("/")):
                raise ValueError("file_paths 必须是工作区内的相对路径")
        return value


TOOL_ARGUMENTS = {
    "read_code": ReadArguments,
    "search_code": SearchArguments,
    "retrieve_repair_experience": RetrieveArguments,
}

FIXED_NODE_TOOLS = frozenset({"apply_patch", "run_tests"})
MODEL_TOOL_ALLOWLIST = frozenset(TOOL_ARGUMENTS)


def validate_tool_arguments(name, arguments, additional_names=()):
    """Validate one decoded tool call and return normalized arguments or safe field errors."""
    if name in FIXED_NODE_TOOLS:
        return None, {"code": "tool_fixed_execution_only", "category": "permission",
                      "message": "本地补丁和测试仅由固定工作流执行；请返回候选补丁，等待程序验证结果"}
    if name not in TOOL_ARGUMENTS and name not in additional_names:
        return None, {"code": "tool_not_allowed", "category": "permission", "message": "工具不在服务端授权列表中"}
    if name not in TOOL_ARGUMENTS:
        return dict(arguments), None
    try:
        return TOOL_ARGUMENTS[name].model_validate(arguments).model_dump(exclude_unset=True), None
    except ValidationError as error:
        issues = [{"path": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                  for item in error.errors(include_input=False, include_url=False)]
        return None, {"code": "tool_arguments_invalid", "category": "validation",
                      "message": "工具参数未通过校验", "issues": issues}

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


def generate_object(prompt, call, contract, *, validate=None, max_corrections=10):
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
