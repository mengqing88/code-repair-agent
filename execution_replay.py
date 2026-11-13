"""Offline evidence replay. This module never constructs execution services."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from execution_manifest import sha256, verify, write_once
from resilience import digest, Fault


def events_at(directory):
    path = Path(directory) / "trace.jsonl"
    if not path.exists():
        return []
    try:
        data = path.read_text(encoding="utf-8")
        if data and not data.endswith("\n"):
            raise ValueError("Trace 最后一条记录未提交")
        records = [json.loads(line) for line in data.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in records):
            raise ValueError("Trace 事件必须为对象")
        return records
    except (ValueError, UnicodeError):
        raise Fault("replay_trace_invalid", "Trace 不完整或损坏，无法回放", category="validation") from None


def evidence(task, directory):
    directory = Path(directory)
    records = events_at(directory)
    artifacts = []
    candidates = [directory / name for name in ("execution-manifest.json", "knowledge.snapshot.sqlite3",
                 "mcp-config.snapshot.json", "mcp-tools.snapshot.json", "validation.md")]
    for folder in ("reproduction", "regression", "node-recordings"):
        candidates.extend((directory / folder).rglob("*"))
    for path in sorted(set(candidates)):
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()):
            artifacts.append({"path": path.relative_to(directory).as_posix(), "sha256": sha256(path)})
    return {"manifest_hash": task["config"]["execution_manifest_hash"],
            "trace_sha256": sha256(directory / "trace.jsonl") if records else None,
            "calls": [{key: event.get(key) for key in ("event_id", "call_id", "parent_call_id", "operation",
                       "event", "parameter_digest", "model_call_id", "manifest_hash")}
                      for event in records], "artifacts": artifacts}


def replay(task, directory):
    """Rehydrate committed node states and recompute routes, without performing any actions."""
    from repair_workflow import NODES, next_node_after
    manifest = verify(task, directory)
    records = events_at(directory)
    identifier = uuid4().hex
    state = deepcopy(manifest["task"]["initial_state"])
    state["task_id"] = task["state"]["task_id"]
    differences, steps, observations = [], [], []
    pending = set()
    for event in records:
        call_key = (event.get("call_id"), event.get("operation"))
        if event.get("event") == "started":
            pending.add(call_key)
        elif event.get("event") in ("succeeded", "failed", "finished", "paused"):
            pending.discard(call_key)
        if event.get("manifest_hash") not in (None, task["config"]["execution_manifest_hash"]):
            differences.append({"event_id": event.get("event_id"), "reason": "manifest_hash_mismatch"})
        if event.get("operation") == "checkpoint" and event.get("event") == "committed":
            saved = event["result"]
            state = deepcopy(saved["state"])
            node = event["details"]["node"]
            actual = next_node_after(node, state)
            if actual != saved["next_node"]:
                differences.append({"event_id": event["event_id"], "reason": "route_changed",
                                    "recorded": saved["next_node"], "current": actual})
            steps.append({"event_id": event["event_id"], "node": node,
                          "status": state["status"], "next_node": actual})
        if event.get("operation") in ("model.generate", "model.tools", "model.tool", "validation.check", "react.model"):
            observations.append(deepcopy(event))
    # Only compare fields committed by nodes. Wall time/accounting are settled again by the worker.
    keys = ("status", "patch_hash", "diagnosis", "patch_diff", "validation", "attempt_count", "memory_saved")
    if steps:
        differences.extend({"field": key, "reason": "final_state_differs"}
                           for key in keys if state.get(key) != task["state"].get(key))
    from workflow_replay import replay_recording
    node_replays = []
    for event in records:
        if event.get("operation") == "workflow.recording" and event.get("event") == "committed":
            saved = event["result"]
            path = Path(directory) / saved["path"]
            if path.is_symlink() or not path.resolve().is_relative_to(Path(directory).resolve()) \
                    or not path.is_file() or sha256(path) != saved["sha256"]:
                differences.append({"reason": "recording_hash_mismatch", "event_id": event.get("event_id")})
                continue
            node_replays.append(replay_recording(path))
    differences.extend({"reason": "node_replay_diverged", "node": item["node"]}
                       for item in node_replays if item["status"] == "diverged")
    from collections import Counter
    expected_nodes = Counter(step["node"] for step in steps if step["node"] in NODES)
    replayed_nodes = Counter(item["node"] for item in node_replays)
    recording_present = any(event.get("operation") == "workflow.recording" for event in records)
    missing_recordings = recording_present and expected_nodes != replayed_nodes
    unavailable = any(item["status"] == "unavailable" for item in node_replays) or missing_recordings
    result = {"replay_id": identifier, "mode": "recorded_results", "source_task_id": task["state"]["task_id"],
              "manifest_hash": task["config"]["execution_manifest_hash"],
              "status": "diverged" if differences else "replayed" if steps and not pending and not unavailable else "incomplete",
              "node_replays": node_replays,
              "deterministic_node_replay": bool(node_replays) and bool(steps) and not pending and not unavailable and not differences,
              "recording_coverage_complete": bool(node_replays) and not missing_recordings,
              "unclosed_calls": [list(key) for key in sorted(pending, key=str)],
              "steps": steps, "observations": observations, "state": state, "differences": differences,
              "external_calls": 0, "evidence": evidence(task, directory),
              "limitations": "新记录重执行节点编排代码，服务结果、时钟和消息ID来自记录；不重跑模型或工具内部逻辑；旧任务仅状态回放，脱敏记录不能完整回放"}
    write_once(Path(directory) / "replays" / f"{identifier}.json", result)
    return result
