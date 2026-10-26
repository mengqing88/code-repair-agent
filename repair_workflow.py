"""用 LangGraph 编排有限的代码修复流程。

本模块只负责节点、状态更新和条件边；真实的复现、检索、模型调用、补丁应用、测试执行
和案例保存通过 RepairServices 注入。这样流程可以先用 FakeAI 离线测试，再替换成真实服务。
"""

# Callable：标注服务回调的输入和返回值；dataclass：组织不可变配置对象；Mapping：表示字典式返回值。
from dataclasses import dataclass
from typing import Callable, Mapping

# END、START：LangGraph 图的结束和起点；StateGraph：根据状态类型创建状态图。
from langgraph.graph import END, START, StateGraph

# RepairState：所有节点共享的状态结构。
from repair_state import RepairState


class WorkflowContractError(RuntimeError):
    """服务回调没有遵守工作流约定时抛出的错误。"""


@dataclass(frozen=True)
class RepairServices:
    """工作流依赖的外部动作；每个回调接收当前状态并返回本节点需要的数据。"""

    # reproduce：在固定基线中复现错误；返回 matched、record，可选 question。
    reproduce: Callable[[RepairState], Mapping[str, object]]
    # collect_context：读取代码证据并检索相关修复案例。
    collect_context: Callable[[RepairState], Mapping[str, object]]
    # propose_patch：可在这里调用 LLM，返回根因、补丁文本和补丁哈希。
    propose_patch: Callable[[RepairState], Mapping[str, object]]
    # apply_patch：将候选补丁应用到隔离工作区；失败时应抛出异常。
    apply_patch: Callable[[RepairState], None]
    # validate_patch：运行固定测试并返回绑定当前补丁哈希的验证摘要。
    validate_patch: Callable[[RepairState], Mapping[str, object]]
    # save_case：人工接受后保存修复经验；失败时不能标记任务成功。
    save_case: Callable[[RepairState], None]


def _mapping(value: object, name: str) -> dict[str, object]:
    """把服务返回值转换成普通字典；返回值不是字典时停止图流程。"""
    if not isinstance(value, Mapping):
        raise WorkflowContractError(f"{name} 必须返回字典")
    return dict(value)


def _required_string(data: Mapping[str, object], name: str) -> str:
    """读取服务返回的必需非空字符串字段。"""
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowContractError(f"{name} 必须是非空字符串")
    return value


def _optional_question(data: Mapping[str, object]) -> str | None:
    """读取可选的 question；缺失或空字符串表示本节点不需要询问用户。"""
    value = data.get("question")
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowContractError("question 必须是字符串或 None")
    return value.strip() or None


def baseline_reproduction_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """执行基线复现；成功后进入上下文收集，无法确认时暂停等待用户。"""
    result = _mapping(services.reproduce(state), "reproduce")
    matched = result.get("matched")
    record = result.get("record")
    if not isinstance(matched, bool) or not isinstance(record, Mapping):
        raise WorkflowContractError("reproduce 必须返回 bool 类型 matched 和字典类型 record")

    update: dict[str, object] = {"reproduction": dict(record), "question": None}
    question = _optional_question(result)
    if question is not None:
        update["question"] = question
    if matched and question is None:
        update["status"] = "retrieving_memory"
    else:
        update["status"] = "needs_input"
        update["question"] = update["question"] or "基线复现未能确认原始问题，请补充触发条件。"
    return update


def collect_context_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """读取代码证据并检索案例；没有案例仍然可以继续常规诊断。"""
    result = _mapping(services.collect_context(state), "collect_context")
    evidence = result.get("code_evidence", [])
    matches = result.get("case_matches", [])
    if not isinstance(evidence, list) or not isinstance(matches, list):
        raise WorkflowContractError("collect_context 必须返回列表类型的 code_evidence 和 case_matches")
    update: dict[str, object] = {"code_evidence": evidence, "case_matches": matches, "question": None}
    question = _optional_question(result)
    if question is not None:
        update["question"] = question
        update["status"] = "needs_input"
    else:
        update["status"] = "repairing"
    return update


def analyze_and_propose_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """调用模型或其他诊断服务，生成当前尝试的根因说明和候选补丁。"""
    result = _mapping(services.propose_patch(state), "propose_patch")
    update = {
        "diagnosis": _required_string(result, "diagnosis"),
        "patch_diff": _required_string(result, "patch_diff"),
        "patch_hash": _required_string(result, "patch_hash"),
        "status": "repairing",
        "last_error": None,
    }
    return update


def apply_patch_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """应用候选补丁；补丁应用失败时结束任务，不让验证节点误报结果。"""
    try:
        services.apply_patch(state)
    except Exception as error:  # noqa: BLE001 - 工作流需要把服务失败写入状态。
        return {"status": "failed", "last_error": f"补丁应用失败：{error}"}
    return {"status": "validating", "last_error": None}


def validate_patch_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """运行独立测试并按结果决定审核、重试或失败。"""
    try:
        validation = _mapping(services.validate_patch(state), "validate_patch")
    except Exception as error:  # noqa: BLE001 - 环境或测试服务失败必须留下可解释状态。
        validation = {
            "patch_hash": state["patch_hash"] or "",
            "passed": False,
            "summary": f"验证服务失败：{error}",
            "report_path": "",
        }

    expected_hash = state["patch_hash"]
    if validation.get("patch_hash") != expected_hash:
        return {
            "status": "failed",
            "validation": validation,
            "last_error": "验证结果对应的补丁哈希与当前补丁不一致。",
        }
    passed = validation.get("passed")
    if not isinstance(passed, (bool, type(None))):
        return {
            "status": "failed",
            "validation": validation,
            "last_error": "验证结果的 passed 字段必须是 True、False 或 None。",
        }

    update: dict[str, object] = {"validation": validation, "question": None}
    if passed is True:
        update["status"] = "reviewable"
        return update

    remaining = dict(state["remaining_budget"])
    attempts_left = remaining["attempts"]
    summary = validation.get("summary")
    failure_reason = summary if isinstance(summary, str) and summary else "独立验证未通过。"
    if isinstance(attempts_left, int) and attempts_left > 0:
        remaining["attempts"] = attempts_left - 1
        update.update(
            status="repairing",
            attempt_count=state["attempt_count"] + 1,
            remaining_budget=remaining,
            last_error=failure_reason,
        )
    else:
        update.update(status="failed", last_error=failure_reason)
    return update


def review_patch_node(state: RepairState) -> dict[str, object]:
    """保持审核暂停状态；用户通过更新 review_decision 后重新调用图。"""
    return {"status": "reviewable"}


def save_case_node(state: RepairState, services: RepairServices) -> dict[str, object]:
    """保存用户接受的修复经验；保存失败时保留失败状态和错误原因。"""
    try:
        services.save_case(state)
    except Exception as error:  # noqa: BLE001 - 保存失败不能伪造成成功。
        return {"status": "failed", "last_error": f"保存修复经验失败：{error}"}
    return {"status": "succeeded", "memory_saved": True, "last_error": None}


def reject_patch_node(state: RepairState) -> dict[str, object]:
    """记录用户拒绝结果；不保存修复经验，但保留现有补丁和验证记录。"""
    return {"status": "failed", "last_error": "用户拒绝了候选修复补丁。", "memory_saved": False}


def _start_route(state: RepairState) -> str:
    """决定首次运行或人工审核恢复时的入口节点。"""
    if state["status"] == "queued":
        return "baseline_reproduction"
    if state["status"] == "reviewable":
        return "review_patch"
    return "finish"


def _status_route(state: RepairState, success_target: str) -> str:
    """成功时进入 success_target，其他状态直接结束，防止错误节点继续执行。"""
    return success_target if state["status"] == "retrieving_memory" else "finish"


def _context_route(state: RepairState) -> str:
    """上下文完整时进入补丁提议，否则停在需要用户输入的状态。"""
    return "propose_patch" if state["status"] == "repairing" else "finish"


def _proposal_route(state: RepairState) -> str:
    """补丁字段有效时进入应用节点，否则结束本轮。"""
    return "apply_patch" if state["status"] == "repairing" else "finish"


def _apply_route(state: RepairState) -> str:
    """补丁应用成功时运行测试，失败时结束。"""
    return "validate_patch" if state["status"] == "validating" else "finish"


def _validation_route(state: RepairState) -> str:
    """验证通过进入审核，失败且有预算时回到补丁提议，否则结束。"""
    if state["status"] == "reviewable":
        return "review_patch"
    if state["status"] == "repairing":
        return "propose_patch"
    return "finish"


def _review_route(state: RepairState) -> str:
    """按人工决定进入保存、拒绝或暂停；pending 不会被自动当作接受。"""
    decision = state["review_decision"]
    if decision == "accepted":
        return "save_case"
    if decision == "rejected":
        return "reject_patch"
    return "finish"


def build_repair_workflow(services: RepairServices):
    """构建并编译修复状态图，返回可用 graph.invoke(state) 执行的图对象。"""
    builder = StateGraph(RepairState)
    builder.add_node("baseline_reproduction", lambda state: baseline_reproduction_node(state, services))
    builder.add_node("collect_context", lambda state: collect_context_node(state, services))
    builder.add_node("propose_patch", lambda state: analyze_and_propose_node(state, services))
    builder.add_node("apply_patch", lambda state: apply_patch_node(state, services))
    builder.add_node("validate_patch", lambda state: validate_patch_node(state, services))
    builder.add_node("review_patch", review_patch_node)
    builder.add_node("save_case", lambda state: save_case_node(state, services))
    builder.add_node("reject_patch", reject_patch_node)

    builder.add_conditional_edges(
        START, _start_route,
        {"baseline_reproduction": "baseline_reproduction", "review_patch": "review_patch", "finish": END},
    )
    builder.add_conditional_edges(
        "baseline_reproduction", lambda state: _status_route(state, "collect_context"),
        {"collect_context": "collect_context", "finish": END},
    )
    builder.add_conditional_edges(
        "collect_context", _context_route,
        {"propose_patch": "propose_patch", "finish": END},
    )
    builder.add_conditional_edges(
        "propose_patch", _proposal_route,
        {"apply_patch": "apply_patch", "finish": END},
    )
    builder.add_conditional_edges(
        "apply_patch", _apply_route,
        {"validate_patch": "validate_patch", "finish": END},
    )
    builder.add_conditional_edges(
        "validate_patch", _validation_route,
        {"review_patch": "review_patch", "propose_patch": "propose_patch", "finish": END},
    )
    builder.add_conditional_edges(
        "review_patch", _review_route,
        {"save_case": "save_case", "reject_patch": "reject_patch", "finish": END},
    )
    builder.add_edge("save_case", END)
    builder.add_edge("reject_patch", END)
    return builder.compile()
