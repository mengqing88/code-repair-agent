"""多代理角色、交接消息和角色权限的回归测试。"""

from multi_agent import AgentRole, agent_turn, policy_for, tool_allowed
from repair_state import create_initial_state
from repair_workflow import RepairServices, build_repair_workflow


def test_role_policies_reserve_local_execution_for_fixed_nodes():
    assert all(not policy_for(role).can_write_workspace for role in AgentRole)
    assert tool_allowed("read_code", AgentRole.PATCHER)
    for role in AgentRole:
        assert not tool_allowed("apply_patch", role)
        assert not tool_allowed("run_tests", role)


def test_workflow_records_agent_handoffs_and_reviewer_result():
    state = create_initial_state(
        "multi-agent-task", {"type": "pasted_code"}, "返回值错误",
        remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
    )
    events = []

    def reproduce(value):
        events.append("reproduce")
        return {"matched": True, "record": {"status": "matched"}}

    def collect(value):
        events.append("collect")
        return {"code_evidence": [], "case_matches": []}

    def propose(value):
        events.append("propose")
        return {"diagnosis": "根因", "patch_diff": "diff", "patch_hash": "hash"}

    def apply(value):
        events.append("apply")

    def validate(value):
        events.append("validate")
        return {"patch_hash": value["patch_hash"], "passed": True,
                "summary": "通过", "report_path": "validation.md"}

    services = RepairServices(reproduce, collect, propose, apply, validate, lambda value: None,
        adversarial_review=lambda _: {"approved": True, "summary": "角色交接测试桩", "findings": []})
    result = build_repair_workflow(services).invoke(state)

    assert result["status"] == "reviewable"
    senders = [item["sender"] for item in result["agent_messages"]]
    assert "reproducer" in senders
    assert "investigator" in senders
    assert "experience_retriever" in senders
    assert "patcher" in senders
    assert "validator" in senders
    assert "reviewer" in senders
    assert result["adversarial_review"]["approved"] is True
    assert events == ["reproduce", "collect", "propose", "apply", "validate"]


def test_agent_turn_restores_previous_role_after_nested_call():
    from multi_agent import current_agent_role

    assert current_agent_role() is None
    with agent_turn(AgentRole.INVESTIGATOR, {"task_id": "t"}, node="collect_context"):
        assert current_agent_role() is AgentRole.INVESTIGATOR
    assert current_agent_role() is None


def test_adversarial_reviewer_rejects_and_restores_candidate():
    state = create_initial_state(
        "review-task", {"type": "pasted_code"}, "返回值错误",
        remaining_seconds=60, remaining_tokens=1000, remaining_attempts=0,
    )
    restored = []

    def reproduce(value):
        return {"matched": True, "record": {"status": "matched"}}

    def collect(value):
        return {"code_evidence": [], "case_matches": []}

    def propose(value):
        return {"diagnosis": "根因", "patch_diff": "diff", "patch_hash": "hash"}

    def validate(value):
        return {"patch_hash": value["patch_hash"], "passed": True,
                "summary": "通过", "report_path": "validation.md"}

    services = RepairServices(
        reproduce, collect, propose, lambda value: None, validate, lambda value: None,
        restore_checkpoint=lambda value: restored.append(value["task_id"]),
        adversarial_review=lambda value: {
            "approved": False, "summary": "补丁扩大了修改范围", "findings": ["越界修改"],
        },
    )
    result = build_repair_workflow(services).invoke(state)
    assert result["status"] == "failed"
    assert restored == ["review-task"]
    assert result["adversarial_review"]["approved"] is False
