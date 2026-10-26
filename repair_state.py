"""定义 LangGraph 节点共享的修复状态，以及新任务的初始状态。

RepairState 可直接传给 StateGraph(RepairState)。节点返回局部字典，只更新其中的字段；
本模块不设置累加规则，因此列表等字段默认由新值替换，避免混入上一轮的证据和验证结果。
TypedDict 提供字段类型说明，不会在运行时自动校验数据或阻止字段被修改。
固定任务、权限、验收配置和预算上限仍须由服务端保存、校验，不能以模型返回值为准。
"""

# deepcopy：复制包含字典和列表的输入，避免一个任务的修改影响调用方或其他任务。
from copy import deepcopy
# math：检查预算数值是否为有限数，避免 NaN 或无穷大绕过限制。
import math
# Literal：限定字段可使用的具体值，例如任务状态只能使用下面列出的字符串。
from typing import Literal
# TypedDict：声明字典各字段的名字及类型；使用 typing_extensions 兼容 LangGraph 的类型解析。
from typing_extensions import TypedDict


# 默认预算：新任务没有显式传入预算时，使用项目约定的限制。
DEFAULT_MAX_REPAIR_ATTEMPTS = 10
DEFAULT_MAX_RUNTIME_SECONDS = 120.0
DEFAULT_MAX_MODEL_TOKENS = 20_000
DEFAULT_MAX_COST_YUAN = 0.10


# RepairStatus：流程阶段和结束原因；reviewable 只表示可供审核，尚不代表用户已接受。
RepairStatus = Literal[
    "queued", "reproducing", "retrieving_memory", "diagnosing", "repairing",
    "validating", "reviewable", "succeeded", "failed", "needs_input",
    "budget_exceeded", "cancelled", "timed_out",
]


class CodeEvidence(TypedDict):
    """一条可定位到源码的证据；诊断文字不能替代实际代码引用。"""

    # path：源码副本中的相对文件路径，例如 src/example.py。
    path: str
    # start_line、end_line：引用的起止行号，从 1 开始，包含首尾两行。
    start_line: int
    end_line: int
    # quote：该位置实际代码的原文；reason：收集这段代码的用途或依据。
    quote: str
    reason: str


class CaseMatch(TypedDict):
    """候选修复案例的固定版本及当前适用性判断；召回命中不等于适用。"""

    # case_id：案例标识；revision：本次读取的案例版本号，避免引用到后来改写的版本。
    case_id: str
    revision: int
    # decision：待判断、适用、不适用、信息不足；Literal 只说明允许值，不自动执行判断。
    decision: Literal["pending", "applicable", "rejected", "inconclusive"]
    # matched_conditions：满足的条件；conflicts：已发现的冲突；unknowns：仍缺少的事实。
    matched_conditions: list[str]
    conflicts: list[str]
    unknowns: list[str]


class ValidationSummary(TypedDict):
    """独立验证产生的摘要，绑定实际受测补丁；由验证器填写，不能由模型宣称通过。"""

    # patch_hash：受测补丁的哈希；不能把旧补丁的验证记录当成新补丁的结果。
    patch_hash: str
    # passed：True 表示验证通过，False 表示未通过，None 表示无法得出有效结论。
    passed: bool | None
    # summary：真实测试结果的文字摘要；report_path：完整验证报告的路径。
    summary: str
    report_path: str


class RemainingBudget(TypedDict):
    """本次任务尚可使用的预算快照；实际扣减及是否停止由后续控制器负责。"""

    # seconds：剩余运行秒数；tokens：剩余模型 token 数；attempts：剩余修复尝试次数。
    seconds: float
    tokens: int
    attempts: int
    # cost：剩余模型费用上限，单位为人民币元；实际费用由模型调用回调提供。
    cost: float


class RepairState(TypedDict):
    """贯穿复现、诊断、修复和验证的共享字典；尚未产生的单个结果使用 None。"""

    # task_id：任务唯一标识，用来关联服务端保存的固定配置及后续执行记录。
    task_id: str
    # source：沿用 CLI 的代码来源元数据，兼容单文件、粘贴代码和固定版本 Git 仓库。
    # 仓库的固定 commit 保存在 source["revision"] 中；此处保存输入快照，不重新选择版本。
    source: dict[str, object]
    # code_text：粘贴模式的 Python 源码，其他来源为 None。
    code_text: str | None
    # original_report：用户原始错误报告，不用补全报告或模型诊断替换它。
    original_report: str
    # status：当前流程阶段或结束原因，取值见 RepairStatus。
    status: RepairStatus
    # reproduction：reproduce() 返回的完整记录，包含修订报告、步骤、真实日志和工作文档路径。
    # 保留现有记录结构及其中文 status；顶层 status 单独表示整个修复流程的状态。
    reproduction: dict[str, object] | None
    # code_evidence：当前诊断使用的代码证据；case_matches：当前案例候选及适用性判断。
    code_evidence: list[CodeEvidence]
    case_matches: list[CaseMatch]
    # diagnosis：有证据支持的根因说明，未诊断时为 None。
    diagnosis: str | None
    # patch_diff：候选补丁正文；patch_hash：同一补丁的哈希，尚未生成时均为 None。
    patch_diff: str | None
    patch_hash: str | None
    # validation：当前补丁的独立验证摘要，尚未执行时为 None，不能预设为验证通过。
    validation: ValidationSummary | None
    # attempt_count：已经进行的修复尝试次数，初始为 0；后续节点负责显式更新。
    attempt_count: int
    # elapsed_seconds：已经消耗的自动执行时间，人工审核等待不由状态节点计入。
    elapsed_seconds: float
    # remaining_budget：剩余预算快照，不包含或修改服务端的原始预算上限。
    remaining_budget: RemainingBudget
    # question：需要向用户确认的具体问题；last_error：流程异常的说明，没有时为 None。
    question: str | None
    last_error: str | None
    # review_decision：人工审核结果；pending 表示工作流已暂停等待用户，不能自动当作接受。
    review_decision: Literal["pending", "accepted", "rejected"]
    # memory_saved：用户接受后是否已经保存修复经验，初始为 False。
    memory_saved: bool


def create_initial_state(
    task_id: str, source: dict[str, object], error_report: str, *,
    remaining_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
    remaining_tokens: int = DEFAULT_MAX_MODEL_TOKENS,
    remaining_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
    remaining_cost: float = DEFAULT_MAX_COST_YUAN,
    code_text: str | None = None,
) -> RepairState:
    """把经过输入校验的任务转换成待执行状态，不调用模型，也不执行复现或修复。

    参数：
        task_id：由调用方分配的任务唯一标识。
        source：CLI 校验后的代码来源元数据；仓库来源须已包含固定 revision。
        error_report：原始错误报告文字，原样保存。
        remaining_seconds：任务当前剩余的运行秒数，默认 120 秒。
        remaining_tokens：任务当前剩余的模型 token 数，默认 20,000。
        remaining_attempts：任务当前剩余的修复尝试次数，默认 10 次。
        remaining_cost：任务当前剩余的模型费用，单位为元，默认 0.10 元。
        code_text：粘贴模式的源码字符串，其他模式不传，默认 None。
        *：表示其后的预算和源码参数必须写参数名，防止把多个数值的位置传反。

    返回：
        新的 RepairState 字典；可变容器互相独立，未产生的结果使用 None 或空列表。
        预算数值不符合要求时抛出 ValueError，避免非法预算进入工作流。
    """
    # isinstance(..., bool) 单独排除布尔值，因为 bool 虽然是 int 的子类，但不是有效预算。
    if (
        isinstance(remaining_seconds, bool)
        or not isinstance(remaining_seconds, (int, float))
        or not math.isfinite(float(remaining_seconds))
        or remaining_seconds <= 0
    ):
        raise ValueError("remaining_seconds 必须是大于 0 的有限数")
    if (
        isinstance(remaining_tokens, bool)
        or not isinstance(remaining_tokens, int)
        or remaining_tokens < 0
    ):
        raise ValueError("remaining_tokens 必须是非负整数")
    if (
        isinstance(remaining_attempts, bool)
        or not isinstance(remaining_attempts, int)
        or remaining_attempts < 0
    ):
        raise ValueError("remaining_attempts 必须是非负整数")
    if (
        isinstance(remaining_cost, bool)
        or not isinstance(remaining_cost, (int, float))
        or not math.isfinite(float(remaining_cost))
        or remaining_cost < 0
    ):
        raise ValueError("remaining_cost 必须是非负有限数")

    # deepcopy(source) 的参数 source 是待复制的来源字典，返回包含内部容器在内的独立副本。
    # 字典、列表字面量每次调用都会新建；调用方之后修改 source 不会影响这里保存的输入快照。
    return {
        "task_id": task_id,
        "source": deepcopy(source),
        "code_text": code_text,
        "original_report": error_report,
        "status": "queued",
        "reproduction": None,
        "code_evidence": [],
        "case_matches": [],
        "diagnosis": None,
        "patch_diff": None,
        "patch_hash": None,
        "validation": None,
        "attempt_count": 0,
        "elapsed_seconds": 0.0,
        "remaining_budget": {
            "seconds": float(remaining_seconds),
            "tokens": remaining_tokens,
            "attempts": remaining_attempts,
            "cost": float(remaining_cost),
        },
        "question": None,
        "last_error": None,
        "review_decision": "pending",
        "memory_saved": False,
    }
