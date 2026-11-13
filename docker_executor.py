"""Run untrusted repair reproductions and tests in a constrained Docker container."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from resilience import Fault, current, run_process, trace
from runtime_config import active_manifest


DEFAULT_IMAGE = "code-repair-sandbox:py311"
_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")
_HIDDEN_DIRECTORIES = {".aws", ".azure", ".docker", ".gnupg", ".kube", ".ssh"}


@dataclass(frozen=True)
class BindMount:
    source: Path
    target: str
    read_only: bool = False


def get_execution_backend(value: str | None = None) -> str:
    """Return the configured execution backend; Docker is the production default."""
    backend = (value if value is not None else os.environ.get("REPAIR_EXECUTION_MODE", "docker")).strip().lower()
    if backend not in ("docker", "host"):
        raise Fault("invalid_execution_mode", "REPAIR_EXECUTION_MODE 只能是 docker 或 host",
                    category="validation", next_action="修正执行模式配置后重试")
    return backend


def configured_image_for_language(language: str, settings: dict[str, str] | None = None) -> str:
    """Resolve a trusted service-side Docker image profile for the task language."""

    from language_support import normalize_language
    from runtime_config import setting

    configured = setting("REPAIR_DOCKER_IMAGES", settings) or ""
    try:
        profiles = json.loads(configured) if configured else {}
    except ValueError as error:
        raise Fault("invalid_runtime_profiles", "REPAIR_DOCKER_IMAGES 必须是 JSON 对象",
                    category="validation") from error
    if not isinstance(profiles, dict) or any(not isinstance(name, str) or not isinstance(image, str)
                                             for name, image in profiles.items()):
        raise Fault("invalid_runtime_profiles", "REPAIR_DOCKER_IMAGES 必须是语言到镜像名的 JSON 对象",
                    category="validation")
    normalized = normalize_language(language)
    image = next((value.strip() for name, value in profiles.items()
                  if normalize_language(name) == normalized), "")
    if not image and normalized == "python":
        image = (setting("REPAIR_DOCKER_IMAGE", settings) or DEFAULT_IMAGE).strip()
    if not image:
        raise Fault("runtime_image_unconfigured", f"没有为 {normalized} 配置 Docker 运行镜像",
                    category="validation",
                    next_action=f"在 REPAIR_DOCKER_IMAGES 中为 {normalized} 配置已安装语言运行时和依赖的镜像")
    if not _IMAGE_PATTERN.fullmatch(image):
        raise Fault("invalid_sandbox_image", f"{normalized} 的 Docker 镜像名格式无效", category="validation")
    return image


def _task_label(task_id: str | None) -> str:
    context = current()
    value = task_id or (context.task_id if context else None) or uuid4().hex
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:128] or uuid4().hex


def _docker_binary() -> str:
    binary = shutil.which("docker")
    if not binary:
        raise Fault("docker_unavailable", "找不到 Docker CLI，无法启动隔离执行容器",
                    category="transient", next_action="安装 Docker Desktop 并启动 Docker Engine")
    return binary


def _cli(command: list[str], timeout: float, *, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
    capture_output = stdout is subprocess.PIPE and stderr is subprocess.PIPE
    return run_process(command, capture_output=capture_output, stdout=None if capture_output else stdout,
                       stderr=None if capture_output else stderr, text=not capture_output,
                       encoding="utf-8" if capture_output else None,
                       errors="replace" if capture_output else None, check=False, timeout=timeout)


def cleanup_docker_task(task_id: str) -> bool:
    """Stop any container left behind if a supervising worker was killed."""
    try:
        binary = _docker_binary()
        label = f"coderepair.task={_task_label(task_id)}"
        found = _cli([binary, "ps", "--all", "--quiet", "--filter", f"label={label}"], 5)
        if found.returncode != 0:
            return False
        identifiers = found.stdout.split()
        if not identifiers:
            return True
        removed = _cli([binary, "rm", "--force", *identifiers], 15)
        return removed.returncode == 0
    except Exception as error:
        trace("sandbox.cleanup", "failed", fault=error, decision="manual_check")
        return False


def _docker_image() -> str:
    manifest = active_manifest()
    if manifest is not None:
        container = manifest["program"]["container"]
        image = container.get("digest") or container.get("image_id")
        if not image:
            raise Fault("image_identity_unresolved", "冻结清单没有可用镜像 digest 或 image ID", category="validation")
        return image
    image = os.environ.get("REPAIR_DOCKER_IMAGE", DEFAULT_IMAGE).strip()
    if not _IMAGE_PATTERN.fullmatch(image):
        raise Fault("invalid_sandbox_image", "REPAIR_DOCKER_IMAGE 格式无效", category="validation",
                    next_action="使用合法的 Docker 镜像名和标签")
    return image


def _mount_argument(mount: BindMount) -> str:
    source = mount.source.expanduser().resolve()
    if not source.exists():
        raise Fault("sandbox_mount_missing", "隔离执行所需的任务文件不存在", category="filesystem")
    if "," in str(source) or "," in mount.target or not mount.target.startswith("/"):
        raise Fault("sandbox_mount_invalid", "隔离执行挂载路径格式无效", category="validation")
    mode = ",readonly" if mount.read_only else ""
    return f"type=bind,source={source.as_posix()},target={mount.target}{mode}"


def _linked_worktree_mounts(workspace: Path, overlay: Path) -> list[BindMount]:
    git_file = workspace / ".git"
    if not git_file.is_file():
        return []
    text = git_file.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        raise Fault("worktree_metadata_invalid", "任务 worktree 的 Git 元数据无效", category="filesystem")
    raw_admin = Path(text.partition(":")[2].strip())
    admin = (raw_admin if raw_admin.is_absolute() else workspace / raw_admin).resolve()
    common_file = admin / "commondir"
    if not admin.is_dir() or not common_file.is_file():
        raise Fault("worktree_metadata_invalid", "任务 worktree 缺少私有 Git 元数据", category="filesystem")
    common = (admin / common_file.read_text(encoding="utf-8").strip()).resolve()
    expected_common = (workspace.parent / f".{workspace.name}-repository.git").resolve()
    if common != expected_common:
        raise Fault("worktree_metadata_outside_task", "Docker 只允许挂载本任务创建的私有 Git 仓库",
                    category="permission")
    try:
        relative_admin = admin.relative_to(common)
    except ValueError:
        raise Fault("worktree_metadata_outside_task", "拒绝把任务目录之外的 Git 元数据挂载到容器",
                    category="permission") from None
    if len(relative_admin.parts) != 2 or relative_admin.parts[0] != "worktrees":
        raise Fault("worktree_metadata_invalid", "任务 worktree 的 Git 元数据路径无效", category="filesystem")

    container_admin = overlay / "worktree-admin"
    shutil.copytree(admin, container_admin)
    (container_admin / "gitdir").write_text("/workspace/.git\n", encoding="ascii")
    pointer = overlay / "worktree.git"
    pointer.write_text(f"gitdir: /repair-git/{relative_admin.as_posix()}\n", encoding="ascii")
    return [
        BindMount(common, "/repair-git", read_only=True),
        BindMount(container_admin, f"/repair-git/{relative_admin.as_posix()}"),
        BindMount(pointer, "/workspace/.git", read_only=True),
    ]


def _sensitive_mounts(workspace: Path, overlay: Path) -> list[BindMount]:
    from input_protection import sensitive_path
    mounts: list[BindMount] = []
    count = 0
    for directory, names, files in os.walk(workspace, followlinks=False):
        root = Path(directory)
        retained = []
        for name in names:
            path = root / name
            if (name in _HIDDEN_DIRECTORIES or sensitive_path(name)) and not path.is_symlink():
                hidden = overlay / f"hidden-dir-{count}"
                hidden.mkdir()
                relative = path.relative_to(workspace).as_posix()
                mounts.append(BindMount(hidden, f"/workspace/{relative}", read_only=True))
                count += 1
            elif not path.is_symlink():
                retained.append(name)
        names[:] = retained
        for name in files:
            path = root / name
            if sensitive_path(name):
                hidden = overlay / f"hidden-file-{count}"
                hidden.touch()
                relative = path.relative_to(workspace).as_posix()
                mounts.append(BindMount(hidden, f"/workspace/{relative}", read_only=True))
                count += 1
                if path.is_symlink():
                    resolved = path.resolve()
                    if resolved.is_file() and resolved.is_relative_to(workspace):
                        resolved_hidden = overlay / f"hidden-file-{count}"
                        resolved_hidden.touch()
                        mounts.append(BindMount(
                            resolved_hidden, f"/workspace/{resolved.relative_to(workspace).as_posix()}",
                            read_only=True,
                        ))
                        count += 1
    return mounts


def run_in_docker(
    command: list[str],
    *,
    workspace: Path,
    timeout: float,
    extra_mounts: tuple[BindMount, ...] = (),
    task_id: str | None = None,
    image: str | None = None,
    stdout=None,
    stderr=None,
):
    """Run a command with no network, host environment, extra capabilities, or writable root FS."""
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        raise Fault("sandbox_workspace_missing", "隔离执行工作区不存在", category="filesystem")
    binary = _docker_binary()
    image = image or _docker_image()
    if not _IMAGE_PATTERN.fullmatch(image):
        raise Fault("invalid_sandbox_image", "Docker 镜像名格式无效", category="validation")
    inspected = _cli([binary, "image", "inspect", image], 15)
    if inspected.returncode != 0:
        detail = (inspected.stderr or inspected.stdout or "").lower()
        unavailable = any(fragment in detail for fragment in (
            "cannot connect", "failed to connect", "is the docker daemon running", "system cannot find the file",
        ))
        code = "docker_unavailable" if unavailable else "sandbox_image_missing"
        message = "Docker Engine 当前不可用" if unavailable else f"隔离执行镜像 {image} 尚未构建"
        action = "启动 Docker Desktop 后重试" if unavailable else "按 docker/runner/README.md 构建隔离执行镜像"
        raise Fault(code, message, category="transient", retryable=True, next_action=action)

    label_value = _task_label(task_id)
    name = f"repair-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix=".repair-docker-", dir=root.parent) as temp_name:
        overlay = Path(temp_name)
        mounts = [BindMount(root, "/workspace")]
        # Model-generated Python can invoke git too: never expose historical objects/credentials.
        # Patch application/checkpoint operations run on the host's private task repository.
        mounts.extend(_sensitive_mounts(root, overlay))
        mounts.extend(extra_mounts)
        docker_command = [binary, "run", "--rm", "--pull=never", "--init", "--name", name,
                          "--label", f"coderepair.task={label_value}", "--network=none",
                          "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                          "--memory=768m", "--memory-swap=768m", "--cpus=1", "--pids-limit=128",
                          "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                          "--env", "PYTHONDONTWRITEBYTECODE=1"]
        if os.name == "nt":
            docker_command.extend(("--user", "0:0"))
        elif hasattr(os, "getuid") and hasattr(os, "getgid"):
            docker_command.extend(("--user", f"{os.getuid()}:{os.getgid()}"))
        for mount in mounts:
            docker_command.extend(("--mount", _mount_argument(mount)))
        docker_command.extend(("--workdir", "/workspace", image, *command))
        trace("sandbox.run", "started", parameters={"image": image, "mount_count": len(mounts)},
              task_id=label_value)
        try:
            result = _cli(docker_command, timeout,
                          stdout=subprocess.PIPE if stdout is None else stdout,
                          stderr=subprocess.PIPE if stderr is None else stderr)
        except subprocess.TimeoutExpired as error:
            if not cleanup_docker_task(label_value):
                raise Fault("sandbox_cleanup_failed", "容器超时后未能确认已停止", category="unknown",
                            outcome_unknown=True, next_action="在 Docker Desktop 中检查 repair 任务容器") from error
            trace("sandbox.run", "timed_out", decision="container_stopped", task_id=label_value)
            raise
        trace("sandbox.run", "finished", decision=result.returncode, task_id=label_value)
        return result


def run_execution(
    command: list[str],
    *,
    workspace: Path,
    timeout: float,
    backend: str | None = None,
    host_env: dict[str, str] | None = None,
    extra_mounts: tuple[BindMount, ...] = (),
    task_id: str | None = None,
    stdout=None,
    stderr=None,
):
    """Dispatch one source-code command to Docker, or to the explicitly selected host mode."""
    mode = get_execution_backend(backend)
    from resilience import current
    context = current()
    if context and context.on_code_execution:
        # This durable intent precedes every user-code command, including a host subprocess.
        context.on_code_execution()
    if mode == "docker":
        return run_in_docker(command, workspace=workspace, timeout=timeout,
                             extra_mounts=extra_mounts, task_id=task_id, stdout=stdout, stderr=stderr)
    capture_output = stdout is None and stderr is None
    return run_process(command, cwd=workspace, env=host_env, capture_output=capture_output,
                       stdout=None if capture_output else stdout, stderr=None if capture_output else stderr,
                       text=capture_output, encoding="utf-8" if capture_output else None,
                       errors="replace" if capture_output else None, timeout=timeout, check=False)
