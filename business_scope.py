"""Deterministic business-scope gate for all user-facing model workflows."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from resilience import Fault


SCOPE_NAME = "software_development"

# A positive signal is deliberately broad: the product supports code generation,
# explanation, review, debugging, and test design across programming languages.
_TECHNOLOGY_SIGNALS = (
    r"\b(?:python|javascript|typescript|java|kotlin|go|golang|rust|c\+\+|c#|ruby|php|swift|sql)\b",
    r"\b(?:api|sdk|json|yaml|schema|mcp|agent|embedding|docker|git|github|pytest|npm|pip|ci/cd|error|exception|stack\s*trace|unit\s*test)\b",
)
_SOFTWARE_CONTEXT = (
    r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception|Test)\b",
    r"\b(?:code|software|program|function|class|module|script|repository|dependency|frontend|backend|database|website|application)\b",
    r"(?:代码|编程|程序|软件|开发|生成代码|解释代码|代码审查|代码评审|重构|调试|排错|修复|缺陷|bug|异常|报错|堆栈|函数|类|模块|脚本|接口|依赖|仓库|测试|用例|部署|编译|运行时|前端|后端|数据库|提示词|模型调用|工具调用|网站|应用|类型注解|类型提示|代码风格|编码规范)",
)
_DEVELOPMENT_ACTION = (
    r"\b(?:write|generate|explain|review|debug|fix|refactor|test|design|build|implement|compile|deploy|run|query|search|why|how|what\s+is)\b",
    r"(?:写|生成|解释|审查|评审|重构|调试|排错|修复|分析|测试|设计|构建|实现|编译|部署|运行|查询|搜索|为什么|怎么|如何|是什么)",
)
_CODING_PREFERENCE = (
    r"(?:我(?:更)?(?:喜欢|偏好|倾向于)|请(?:记住|遵循)|以后(?:请)?(?:优先|避免|不要)|"
    r"优先使用|避免使用|不要使用).{0,16}(?:类型注解|类型提示|代码风格|编码规范|命名规范|注释风格|格式化)"
)

# These are high-confidence non-development requests. They take precedence over
# a weak incidental word such as "模型" or "测试" in a general question.
_NON_SOFTWARE_SIGNALS = (
    r"(?:写|起草|润色|回复)(?:一封|一条|一份|一首)?\s*(?:道歉|客户|工作)?(?:邮件|短信|简历|情书|诗|歌词|小说|故事)",
    r"\b(?:write|draft|compose)\s+(?:me\s+)?(?:an?\s+)?(?:email|text message|resume|love letter|poem|poetry|story|song)\b",
    r"\b(?:plan|recommend|give me)\s+(?:a\s+)?(?:recipe|travel itinerary|vacation|stock|investment|weather|medical advice|legal advice)\b",
    r"(?:帮我|请|给我|我想要|帮忙).{0,12}(?:规划|安排|推荐|给出|写).{0,8}(?:旅游|旅行|行程|菜谱|食谱|股票|投资|天气|新闻|法律意见|合同审查|医疗建议|诊断病情|诗|歌词|小说|故事|绘画|图片|音乐|健身|减肥计划|租房|买房)",
)


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    reason: str
    signals: tuple[str, ...] = ()


def _text(values: Iterable[object]) -> str:
    return "\n".join(str(value) for value in values if value is not None).strip()


def assess_software_request(*values: object, source_type: str | None = None) -> ScopeDecision:
    """Classify a request without calling a model or external service.

    The classifier is intentionally conservative. An explicit code source is a
    positive signal for repair tasks, while clear non-development intent always
    wins. Ambiguous chat messages are rejected and can be re-submitted with a
    concrete programming goal.
    """
    text = _text(values)
    lowered = text.lower()
    preference_match = re.search(_CODING_PREFERENCE, text, flags=re.IGNORECASE | re.DOTALL)
    scope_text = text
    if preference_match:
        scope_text = text[:preference_match.start()] + " " * len(preference_match.group()) + text[preference_match.end():]
    for pattern in _NON_SOFTWARE_SIGNALS:
        match = re.search(pattern, scope_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            has_software_artifact = any(re.search(item, scope_text, flags=re.IGNORECASE)
                                         for item in _SOFTWARE_CONTEXT[1:])
            if not has_software_artifact:
                return ScopeDecision(False, "explicit_non_software_request", (match.group(0)[:80],))

    signals: list[str] = []
    if source_type in {"python_file", "git_repository", "pasted_code"}:
        signals.append("software_source")
    if preference_match:
        return ScopeDecision(True, "software_development_preference", ("coding_preference",))
    technology = any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _TECHNOLOGY_SIGNALS)
    software_context = any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _SOFTWARE_CONTEXT)
    action = any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _DEVELOPMENT_ACTION)
    if technology:
        signals.append("technology_term")
    if software_context:
        signals.append("software_context")
    if action:
        signals.append("development_intent")
    # A technology name alone is incidental context, not proof of a software task.
    # A recognized software entity (for example TypeError) is sufficiently specific.
    explicit_entity = bool(re.search(_SOFTWARE_CONTEXT[0], text, flags=re.IGNORECASE))
    if source_type in {"python_file", "git_repository", "pasted_code"} or explicit_entity or (
        action and (technology or software_context)
    ) or (technology and software_context):
        return ScopeDecision(True, "software_development_signal", tuple(signals))
    return ScopeDecision(False, "no_software_development_signal")


def require_software_request(*values: object, source_type: str | None = None) -> ScopeDecision:
    """Reject out-of-scope work before any model, embedding, or MCP call."""
    decision = assess_software_request(*values, source_type=source_type)
    if decision.allowed:
        return decision
    raise Fault(
        "business_scope_denied",
        "请求超出软件开发业务范围；请提供代码生成、代码解释、代码审查、调试或测试设计任务",
        category="permission",
        next_action="补充明确的软件开发目标后重新提交",
        details={"scope": SCOPE_NAME, "reason": decision.reason, "signals": list(decision.signals)},
    )
