"""Deployment mode is an independent server boundary, not a model instruction."""
import json

import pytest

from repair_api import RepairRunner, TaskInput
from execution_manifest import freeze, verify
from knowledge_base import KnowledgeBase
from repair_state import create_initial_state
from resilience import Fault


def test_untrusted_mode_rejects_host_execution_and_local_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_DEPLOYMENT_MODE", "untrusted")
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")
    with pytest.raises(ValueError, match="Docker"):
        RepairRunner(tmp_path / "host-data")
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "docker")
    runner = RepairRunner(tmp_path / "docker-data")
    try:
        with pytest.raises(Exception) as error:
            runner.create(TaskInput(source_type="python_file", path="C:/private/bug.py", error_report="代码返回值错误"))
        assert error.value.status_code == 403
        with pytest.raises(Fault, match="旧任务"):
            runner.validate_task_identity({"config": {"execution_backend": "host"}})
    finally:
        runner.pool.shutdown()


def test_untrusted_manifest_disables_host_mcp_and_binds_mode(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [{"name": "host", "command": "dangerous.exe", "allow_tools": ["write"]}]}))
    monkeypatch.setenv("MCP_SERVERS_CONFIG", str(path))
    directory = tmp_path / "task"
    directory.mkdir()
    task = {"state": create_initial_state("task", {"type": "pasted_code"}, "函数错误"), "config": {
        "deployment_mode": "untrusted", "execution_backend": "host", "allowed_paths": ["target.py"],
        "source_fingerprint": {}, "test_targets": ["."]}}
    manifest, _ = freeze(task, directory, {}, KnowledgeBase(tmp_path / "knowledge.sqlite3"))
    assert json.loads((directory / "mcp-config.snapshot.json").read_text())["servers"] == []
    task["config"]["deployment_mode"] = "local"
    with pytest.raises(Fault, match="部署边界"):
        verify(task, directory)
