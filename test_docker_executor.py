from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docker_executor import BindMount, _linked_worktree_mounts, get_execution_backend, run_in_docker
from repair_tools import create_checkpoint_workspace
from resilience import Fault


def test_docker_is_the_default_and_host_mode_must_be_explicit(monkeypatch):
    monkeypatch.delenv("REPAIR_EXECUTION_MODE", raising=False)
    assert get_execution_backend() == "docker"
    assert get_execution_backend("host") == "host"
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "unknown")
    with pytest.raises(Fault, match="只能是 docker 或 host"):
        get_execution_backend()


def test_docker_runner_applies_resource_and_mount_boundaries(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("CHAT_API_KEY=must-not-be-mounted\n", encoding="utf-8")
    (workspace / ".ssh").mkdir()
    (workspace / "test_safe.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    calls = []

    def fake_cli(command, timeout, **kwargs):
        calls.append((command, timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("docker_executor.shutil.which", return_value="docker"), \
         patch("docker_executor._cli", side_effect=fake_cli):
        result = run_in_docker(
            ["python", "-I", "-m", "pytest", "test_safe.py"], workspace=workspace,
            timeout=4, extra_mounts=(BindMount(workspace / "test_safe.py", "/task/test.py", True),),
            task_id="task-1",
        )

    assert result.returncode == 0
    assert calls[0][0][:3] == ["docker", "image", "inspect"]
    command = calls[1][0]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--pids-limit=128" in command
    assert "--memory=768m" in command
    mounts = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--mount"]
    assert any("target=/workspace/.env,readonly" in mount for mount in mounts)
    assert any("target=/workspace/.ssh,readonly" in mount for mount in mounts)
    assert any("target=/task/test.py,readonly" in mount for mount in mounts)
    assert "must-not-be-mounted" not in " ".join(command)


def test_missing_docker_engine_fails_closed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with patch("docker_executor.shutil.which", return_value="docker"), patch(
        "docker_executor._cli",
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="cannot connect to the docker daemon"),
    ):
        with pytest.raises(Fault) as raised:
            run_in_docker(["python", "-m", "pytest"], workspace=workspace, timeout=1)
    assert raised.value.code == "docker_unavailable"


def test_linked_worktree_gets_task_only_git_mounts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost",
        "commit", "-qm", "baseline",
    ], check=True)
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    workspace = tmp_path / "tasks" / "task-1" / "workspace"
    checkpoint = create_checkpoint_workspace(source, workspace, revision=revision)
    overlay = tmp_path / "overlay"
    overlay.mkdir()

    mounts = _linked_worktree_mounts(workspace, overlay)

    assert len(mounts) == 3
    assert mounts[0].source == workspace.parent / ".workspace-repository.git"
    assert mounts[0].read_only is True
    admin_copy = mounts[1].source
    assert (admin_copy / "gitdir").read_text(encoding="ascii") == "/workspace/.git\n"
    assert mounts[2].target == "/workspace/.git"
    assert mounts[2].read_only is True
    assert (workspace / "target.py").read_text(encoding="utf-8") == "value = 1\n"

