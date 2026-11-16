"""受控多代理角色、权限和代理间消息。

代理角色共享任务状态，但不共享未声明的工具权限。工作流仍由确定性的
LangGraph 调度；本模块负责把每个节点标记为一个代理回合，并把交接写入
任务状态和 Trace，避免把自然语言对话当成可靠的流程协议。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator, Mapping, MutableMapping
from uuid import uuid4

from code_repair.resilience import trace, traced_call
from code_repair.model_contracts import FIXED_NODE_TOOLS


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    REPRODUCER = "reproducer"
    INVESTIGATOR = "investigator"
    EXPERIENCE_RETRIEVER = "experience_retriever"
    PATCHER = "patcher"
    VALIDATOR = "validator"
    REVIEWER = "reviewer"


@dataclass(frozen=True)
class AgentPolicy:
    """模型角色的本地工具权限；固定执行节点不通过此表授权。"""

    label: str
    tools: frozenset[str]
    can_write_workspace: bool = False


ROLE_POLICIES: dict[AgentRole, AgentPolicy] = {
    AgentRole.SUPERVISOR: AgentPolicy("调度代理", frozenset()),
    AgentRole.REPRODUCER: AgentPolicy("复现代理", frozenset({"read_code", "search_code"})),
    AgentRole.INVESTIGATOR: AgentPolicy("调查代理", frozenset({"read_code", "search_code"})),
    AgentRole.EXPERIENCE_RETRIEVER: AgentPolicy("经验检索代理", frozenset({"retrieve_repair_experience"})),
    AgentRole.PATCHER: AgentPolicy(
        "补丁代理",
        frozenset({"read_code", "search_code", "retrieve_repair_experience"}),
    ),
    AgentRole.VALIDATOR: AgentPolicy("验证代理", frozenset({"read_code"})),
    AgentRole.REVIEWER: AgentPolicy("对抗审查代理", frozenset({"read_code", "search_code"})),
}

_active_role: ContextVar[AgentRole | None] = ContextVar("repair_active_agent_role", default=None)


def policy_for(role: AgentRole | str) -> AgentPolicy:
    """返回角色策略；未知角色不能静默获得默认权限。"""
    try:
        return ROLE_POLICIES[role if isinstance(role, AgentRole) else AgentRole(role)]
    except (KeyError, ValueError) as error:
        raise ValueError(f"未知代理角色：{role}") from error


def current_agent_role() -> AgentRole | None:
    return _active_role.get()


def tool_allowed(name: str, role: AgentRole | str | None = None) -> bool:
    """检查工具是否在当前代理的显式权限中。未进入代理回合时不改变旧调用方行为。"""
    if name in FIXED_NODE_TOOLS:
        return False
    active = role if role is not None else current_agent_role()
    if active is None:
        return True
    resolved = active if isinstance(active, AgentRole) else AgentRole(active)
    if name.startswith("mcp__"):
        return resolved is AgentRole.PATCHER
    return name in policy_for(resolved).tools


def _message(role: AgentRole, receiver: AgentRole, message_type: str,
             payload: Mapping[str, object], *, node: str) -> dict[str, object]:
    from code_repair.workflow_replay import message_id
    return {
        "message_id": message_id(),
        "sender": role.value,
        "sender_label": policy_for(role).label,
        "receiver": receiver.value,
        "receiver_label": policy_for(receiver).label,
        "type": message_type,
        "node": node,
        "payload": dict(payload),
    }


def append_message(state: Mapping[str, object], role: AgentRole, receiver: AgentRole,
                   message_type: str, payload: Mapping[str, object], *, node: str) -> list[dict[str, object]]:
    """生成追加后的不可变消息列表，供 LangGraph 状态节点返回。"""
    messages = list(state.get("agent_messages") or [])
    item = _message(role, receiver, message_type, payload, node=node)
    messages.append(item)
    trace("agent.message", "sent", details={
        "sender": role.value, "receiver": receiver.value, "type": message_type, "node": node,
    }, result={"message_id": item["message_id"], "payload_keys": sorted(payload)})
    return messages


@contextmanager
def agent_turn(role: AgentRole, state: Mapping[str, object], *, node: str) -> Iterator[dict[str, object]]:
    """建立一个带角色标签的代理回合，并在异常时留下完整失败 Trace。"""
    policy = policy_for(role)
    token = _active_role.set(role)
    try:
        with traced_call(
            "agent.turn",
            parameters={"task_id": state.get("task_id"), "node": node, "role": role.value},
            agent_role=role.value,
            agent_label=policy.label,
            agent_node=node,
        ) as observation:
            yield observation
    finally:
        _active_role.reset(token)


def wrap_agent_result(state: Mapping[str, object], role: AgentRole, node: str,
                      result: Mapping[str, object] | None) -> dict[str, object]:
    """把节点结果和代理交接消息合并，避免代理输出只存在于内存。"""
    update = dict(result or {})
    receiver = AgentRole.SUPERVISOR
    payload = {
        "status": update.get("status", state.get("status")),
        "output_keys": sorted(update),
    }
    message_state = dict(state)
    if isinstance(update.get("agent_messages"), list):
        message_state["agent_messages"] = update["agent_messages"]
    update["agent_messages"] = append_message(message_state, role, receiver, "result", payload, node=node)
    return update
