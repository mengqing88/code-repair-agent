"""Stop repeated action/result cycles before they consume the whole task budget."""
from collections import Counter

from code_repair.resilience import Fault, digest, trace


_DIAGNOSTIC_KEYS = {"error_id", "request_id", "timestamp", "diagnostics_persisted"}


def _stable_error(value):
    if isinstance(value, dict):
        return {key: _stable_error(item) for key, item in value.items() if key not in _DIAGNOSTIC_KEYS}
    if isinstance(value, list):
        return [_stable_error(item) for item in value]
    return value


class ActionGuard:
    def __init__(self, repetitions=3):
        self.repetitions = repetitions
        self.counts = Counter()
        self.history = []

    def observe(self, name, arguments, result):
        observation = result
        if isinstance(result, dict) and isinstance(result.get("error"), dict):
            # Diagnostic identities change per attempt; successful tool data stays intact.
            observation = {**result, "error": _stable_error(result["error"])}
        signature = digest([name, arguments, observation])
        self.counts[signature] += 1
        self.history.append(signature)
        cycle = 0
        for size in range(1, 5):
            tail = self.history[-size * self.repetitions:]
            if len(tail) == size * self.repetitions and tail == tail[:size] * self.repetitions:
                cycle = size
                break
        repeated = self.counts[signature]
        if repeated >= self.repetitions or cycle:
            trace("react.progress", "stopped", decision="repeated_action_result",
                  details={"signature": signature, "repetitions": repeated, "cycle_length": cycle})
            raise Fault("agent_no_progress", "工具动作及返回结果反复重复，已暂停以避免无进展循环",
                        category="validation", next_action="请补充新的线索，或说明需要改变的排查方向")
        if repeated > 1:
            return {"code": "repeated_action_warning", "message": "相同动作没有产生新证据，请改变参数、工具或结束分析"}
        return None
