"""Re-execute orchestration code using recorded services, clocks and message IDs."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields, replace
import json
from pathlib import Path
import time
from uuid import uuid4

from input_protection import protect_input
from resilience import Fault, digest, trace


_directory = ContextVar("workflow_recording_directory", default=None)
_tape = ContextVar("workflow_recording_tape", default=None)


@contextmanager
def recording_scope(directory):
    token = _directory.set(Path(directory))
    try:
        yield
    finally:
        _directory.reset(token)


def recorded_value(kind, produce, *, input_hash=None, atomic=False):
    tape = _tape.get()
    if tape is None:
        return produce()
    if tape["mode"] == "replay":
        position = tape["position"]
        if position >= len(tape["events"]):
            raise Fault("replay_missing_observation", "缺少已记录结果，禁止调用真实服务", category="validation")
        event = tape["events"][position]
        tape["position"] += 1
        if event["kind"] != kind or event.get("input_hash") != input_hash:
            raise Fault("replay_call_changed", "回放的调用或输入与记录不一致", category="validation")
        if event.get("error"):
            error = event["error"]
            if error["type"] == "Fault":
                fault = Fault(**error["arguments"])
                fault.error_id = error["error_id"]
                raise fault
            import builtins
            exception_type = getattr(builtins, error["type"], ValueError)
            if not isinstance(exception_type, type) or not issubclass(exception_type, Exception):
                exception_type = ValueError
            raise exception_type(error["message"])
        return deepcopy(event["value"])
    try:
        suspended = _tape.set(None) if atomic else None
        try:
            value = produce()
        finally:
            if suspended is not None:
                _tape.reset(suspended)
    except Exception as error:
        saved = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, Fault):
            saved["arguments"] = {key: getattr(error, key) for key in (
                "code", "category", "retryable", "outcome_unknown", "next_action", "budget_kind", "causes",
                "retry_after", "operation_id", "issues", "details")}
            saved["arguments"]["message"] = str(error)
            saved["error_id"] = error.error_id
        tape["events"].append({"kind": kind, "input_hash": input_hash, "error": saved})
        raise
    tape["events"].append({"kind": kind, "input_hash": input_hash, "value": deepcopy(value)})
    return value


def workflow_clock():
    return recorded_value("clock", time.monotonic)


def message_id():
    return recorded_value("message_id", lambda: uuid4().hex)


def instrument_services(services):
    def wrap(name, callback):
        return lambda state: recorded_value("service:" + name, lambda: callback(state), input_hash=digest(state), atomic=True)
    return replace(services, **{field.name: wrap(field.name, getattr(services, field.name))
                               for field in fields(services) if callable(getattr(services, field.name))})


@contextmanager
def node_recording(node, state):
    directory = _directory.get()
    if directory is None or _tape.get() is not None:
        yield None
        return
    tape = {"mode": "record", "events": [], "node": node, "input": deepcopy(state)}
    token = _tape.set(tape)
    try:
        yield tape
    finally:
        _tape.reset(token)
        if "output" in tape:
            from execution_manifest import sha256, write_once
            payload = {key: value for key, value in tape.items() if key != "mode"}
            safe = protect_input(payload)
            safe["replayable"] = safe == payload
            path = directory / "node-recordings" / (uuid4().hex + ".json")
            write_once(path, safe)
            trace("workflow.recording", "committed", result={"path": path.relative_to(directory).as_posix(),
                  "sha256": sha256(path), "node": node, "replayable": safe["replayable"]})


def replay_recording(path):
    """Only services in the tape are available; an extra call cannot access the network."""
    from repair_workflow import RepairServices, execute_agent_node
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("replayable"):
        return {"node": data["node"], "status": "unavailable", "reason": "redacted_observations"}
    def forbidden(_):
        raise Fault("replay_external_call_denied", "回放禁止真实外部调用", category="permission")
    services = RepairServices(**{field.name: forbidden for field in fields(RepairServices)})
    tape = {"mode": "replay", "events": data["events"], "position": 0}
    token = _tape.set(tape)
    try:
        result = execute_agent_node(data["node"], deepcopy(data["input"]), instrument_services(services))
        matched = result == data["output"] and tape["position"] == len(tape["events"])
        return {"node": data["node"], "status": "replayed" if matched else "diverged",
                "expected_hash": digest(data["output"]), "actual_hash": digest(result),
                "consumed": tape["position"], "recorded": len(tape["events"])}
    except Exception as error:
        return {"node": data["node"], "status": "diverged", "reason": protect_input(str(error))}
    finally:
        _tape.reset(token)
