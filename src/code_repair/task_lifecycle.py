"""Storage-level lifecycle checks shared by API, workers and recovery."""
from contextlib import contextmanager
from contextvars import ContextVar

from code_repair.resilience import Fault


_intent = ContextVar("task_transition_intent", default="normal")
ACTIVE = {"queued", "reproducing", "retrieving_memory", "diagnosing", "repairing", "validating"}
FAILURES = {"failed", "needs_input", "budget_exceeded", "cancelled", "timed_out"}
STATES = ACTIVE | FAILURES | {"reviewable", "succeeded"}
EDGES = {
    "queued": {"reproducing", "retrieving_memory", "repairing", "validating", "reviewable"},
    "reproducing": {"retrieving_memory"},
    "retrieving_memory": {"diagnosing", "repairing"},
    "diagnosing": {"repairing"},
    "repairing": {"validating"},
    "validating": {"repairing", "reviewable"},
    "reviewable": {"succeeded"},
    "needs_input": {"queued"},
    "failed": set(), "budget_exceeded": set(), "timed_out": set(),
    "cancelled": set(), "succeeded": set(),
}


@contextmanager
def transition_scope(intent):
    if intent not in {"recovery", "budget_extension", "dead_letter_requeue", "tool_outcome"}:
        raise ValueError("unknown transition intent")
    token = _intent.set(intent)
    try:
        yield
    finally:
        _intent.reset(token)


def validate_transition(previous, task):
    state = task["state"]
    target = state["status"]
    if target not in STATES or task.get("execution") not in {"queued", "running", "idle"}:
        raise Fault("invalid_task_transition", "任务状态不在服务端允许范围内", category="validation")
    if previous is None:
        return
    source = previous["state"]["status"]
    intent = _intent.get()
    allowed = target == source or target in EDGES.get(source, set())
    if source not in {"succeeded", "cancelled"} and target in FAILURES:
        allowed = True
    if intent == "recovery" and source not in {"succeeded", "cancelled"}:
        allowed = True
    if intent in {"budget_extension", "dead_letter_requeue"} and source in FAILURES and target == "queued":
        allowed = True
    if not allowed:
        raise Fault("invalid_task_transition", f"禁止任务状态从 {source} 迁移到 {target}", category="conflict")
    old_fault = previous["state"].get("error_info") or {}
    if source == "needs_input" and target == "queued" and old_fault.get("outcome_unknown") and intent != "recovery":
        verified = any(r.get("operation_id") == old_fault.get("operation_id") and r.get("outcome") == "not_applied"
                       for r in state.get("tool_resolutions", []))
        if not old_fault.get("operation_id") and intent == "tool_outcome":
            verified = any(r.get("operation_id") and r.get("outcome") == "not_applied"
                           for r in state.get("tool_resolutions", []))
        restoring_redelivery = (intent == "dead_letter_requeue" and previous.get("redelivery_restore")
                                and not task.get("redelivery_restore")
                                and old_fault.get("code") == "dead_letter_restore_in_progress")
        if not verified and not restoring_redelivery:
            raise Fault("invalid_task_transition", "未知操作结果未核实，禁止进入执行队列", category="conflict")
    if target == "reviewable" and source != "reviewable":
        validation = state.get("validation") or {}
        review = state.get("adversarial_review") or {}
        if (not state.get("patch_hash") or validation.get("passed") is not True
                or validation.get("patch_hash") != state["patch_hash"]
                or review.get("approved") is not True or review.get("patch_hash") != state["patch_hash"]):
            raise Fault("invalid_task_transition", "进入人工审核前缺少同一补丁的验证和独立审查证据", category="conflict")
    if target == "succeeded" and source != "succeeded":
        validation = state.get("validation") or {}
        review = state.get("adversarial_review") or {}
        if (state.get("review_decision") != "accepted" or not state.get("memory_saved")
                or validation.get("passed") is not True or not state.get("patch_hash")
                or validation.get("patch_hash") != state["patch_hash"]
                or review.get("approved") is not True or review.get("patch_hash") != state["patch_hash"]):
            raise Fault("invalid_task_transition", "完成任务缺少同一补丁的验证、独立审查或人工接受证据", category="conflict")
    if source in {"succeeded", "cancelled"} and task["state"] != previous["state"]:
        # Accounting/diagnostics may still be persisted after a terminal node.
        ignored = {"accounting", "warnings", "elapsed_seconds", "remaining_budget"}
        if {k:v for k,v in state.items() if k not in ignored} != {k:v for k,v in previous["state"].items() if k not in ignored}:
            raise Fault("invalid_task_transition", "终态任务不能修改执行结果", category="conflict")
