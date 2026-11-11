"""为代码修复工作流提供受限工具。

工具只操作指定工作区。模型可读取、搜索、检索经验，也可在范围检查和测试保护下试用补丁、运行固定测试；
最终补丁验证与经验保存仍由固定工作流节点负责。
"""

from __future__ import annotations

# json：编码知识库文档和工具参数；math：检查向量中的有限数字。
import json
import hashlib
import math
# os、shutil、subprocess：读取运行环境、查找 rg、执行受限的 git/pytest 子进程。
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
# dataclass：保存工具箱配置；PurePosixPath：按 Git 的斜杠路径解析补丁文件名。
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
# re：解析 unified diff 的文件头；typing：标注工具输入输出。
import re
from typing import Any, Callable, Mapping, Sequence

from dotenv import dotenv_values
from pydantic import ValidationError

from knowledge_base import (
    KnowledgeBaseError, _case_search_text, _normalize_retrieval_filters,
    build_case_document, build_rejection_document,
)
from defect_regression import generate_regression_test, reproduction_exit_matches_test
from docker_executor import BindMount, get_execution_backend, run_execution
from llm_client import embed_text
from model_contracts import TOOL_ARGUMENTS, MODEL_TOOL_ALLOWLIST
from mcp_tool_client import MCPToolClient
from resilience import Fault, cleanup_scope, trace, run_process, error_info, traced_call
from reproduction import execute_reproduction, fenced
from repair_state import RepairState


class RepairToolError(Fault):
    """工具输入、权限范围或外部程序执行失败时抛出的错误。"""
    def __init__(self, message):
        super().__init__("tool_rejected", message, category="business", next_action="检查路径、权限及业务条件，修正后重新规划")


EmbeddingFunction = Callable[[str], Sequence[float]]
ConnectionFactory = Callable[[str], Any]


def _task_copy_ignored(directory: str, names: list[str]) -> set[str]:
    """复制非 Git 输入时排除工作目录、缓存和本地凭据文件。"""
    ignored = {
        ".git", ".venv", "venv", "__pycache__", ".idea", "work",
        "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".ssh", ".aws", ".azure", ".gnupg", ".kube",
    }
    return {
        name for name in names
        if name in ignored or name == ".env" or (name.startswith(".env.") and name != ".env.example")
    }


def create_checkpoint_workspace(
    source_root: Path,
    workspace_root: Path,
    *,
    revision: str | None = None,
    source_file: Path | None = None,
    pasted_code: str | None = None,
) -> str:
    """从固定 Git commit 或当前文件快照创建隔离工作区，并返回基线 checkpoint。

    参数：
        source_root：原仓库或代码目录；只读取其中的输入，不在其中应用补丁。
        workspace_root：新建的任务工作区路径，必须尚不存在。
        revision：Git 仓库模式使用的完整 commit；省略时复制当前目录快照。
        source_file：单文件模式下需要复制到工作区的源码路径。
        pasted_code：粘贴代码模式下写入工作区 target.py 的源码。

    返回：
        Git commit 哈希；后续可用它恢复该工作区的文件状态。
    """
    source = source_root.expanduser().resolve()
    workspace = workspace_root.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"代码目录不存在：{source}")
    if workspace.exists():
        raise ValueError(f"任务工作区已经存在：{workspace}")
    if source.is_relative_to(workspace):
        raise ValueError("任务工作区不能包含原始代码目录")

    workspace.parent.mkdir(parents=True, exist_ok=True)
    repository = workspace.parent / f".{workspace.name}-repository.git"
    if revision is not None:
        if repository.exists():
            raise ValueError(f"任务 worktree 的私有仓库已经存在：{repository}")
        try:
            completed = run_process(
                ["git", "clone", "--bare", "--no-hardlinks", os.fspath(source), os.fspath(repository)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=120,
            )
            if completed.returncode != 0:
                raise ValueError(f"无法创建任务私有 Git 仓库：{(completed.stderr or completed.stdout).strip()}")
            run_process(
                ["git", "-C", os.fspath(repository), "remote", "remove", "origin"],
                capture_output=True, check=True, timeout=30,
            )
            run_process(
                ["git", "-C", os.fspath(repository), "config", "core.autocrlf", "false"],
                capture_output=True, check=True, timeout=30,
            )
            checkout = run_process(
                ["git", "-C", os.fspath(repository), "worktree", "add", "--detach",
                 os.fspath(workspace), revision],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=30,
            )
            if checkout.returncode != 0:
                raise ValueError(f"无法从固定 commit 创建任务 worktree：{(checkout.stderr or checkout.stdout).strip()}")
        except Exception:
            if workspace.exists():
                run_process(
                    ["git", "-C", os.fspath(repository), "worktree", "remove", "--force", os.fspath(workspace)],
                    capture_output=True, check=False, timeout=30,
                )
                if workspace.exists():
                    shutil.rmtree(workspace)
            if repository.exists():
                shutil.rmtree(repository)
            raise
    else:
        def ignore_copy_directory(directory: str, names: list[str]) -> set[str]:
            """跳过项目临时目录及当前目标路径，避免把工作区复制进自身。"""
            ignored_names = _task_copy_ignored(directory, names)
            # 不把指向原目录的链接带进副本，避免测试沿链接写回原文件。
            for name in set(names) - ignored_names:
                candidate = Path(directory) / name
                if candidate.is_symlink() or getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"代码目录含符号链接或目录联接，无法创建独立快照：{candidate}")
            current_directory = Path(directory).resolve()
            try:
                nested_workspace = workspace.relative_to(current_directory)
            except ValueError:
                return ignored_names
            if nested_workspace.parts:
                ignored_names.add(nested_workspace.parts[0])
            return ignored_names

        try:
            shutil.copytree(source, workspace, ignore=ignore_copy_directory, symlinks=True)
            if source_file is not None:
                original_file = source_file.expanduser().resolve()
                relative_file = original_file.relative_to(source)
                copied_file = workspace / relative_file
                if not copied_file.is_file():
                    raise ValueError(f"代码文件没有复制到任务工作区：{relative_file}")
            if pasted_code is not None:
                (workspace / "target.py").write_text(pasted_code, encoding="utf-8")
            run_process(
                ["git", "init", "--quiet", os.fspath(workspace)],
                capture_output=True, check=True, timeout=30,
            )
            # --force 将快照里原有的忽略文件也纳入基线，恢复时才能保留它们。
            # 关闭自动换行转换，确保 checkpoint 保存复制时的原始字节。
            run_process(
                ["git", "-C", os.fspath(workspace), "config", "core.autocrlf", "false"],
                capture_output=True, check=True, timeout=30,
            )
            run_process(
                ["git", "-C", os.fspath(workspace), "add", "--all", "--force"],
                capture_output=True, check=True, timeout=30,
            )
            run_process(
                [
                    "git", "-C", os.fspath(workspace), "-c", "user.name=Repair Agent",
                    "-c", "user.email=repair-agent@localhost", "commit", "--quiet",
                    "--allow-empty", "-m", "Create task checkpoint",
                ],
                capture_output=True, check=True, timeout=30,
            )
        except Exception:
            if workspace.exists():
                shutil.rmtree(workspace)
            raise

    try:
        completed = run_process(
            ["git", "-C", os.fspath(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=30,
        )
        # marker_path：只允许对带有任务标记的副本执行恢复，避免误用原仓库。
        checkpoint = completed.stdout.strip()
        marker_path = workspace.parent / f".{workspace.name}-repair-checkpoint.json"
        marker_path.write_text(
            json.dumps({"workspace": str(workspace), "checkpoint": checkpoint}, ensure_ascii=False),
            encoding="utf-8",
        )
        return checkpoint
    except Exception:
        if workspace.exists():
            if revision is not None and repository.exists():
                run_process(
                    ["git", "-C", os.fspath(repository), "worktree", "remove", "--force", os.fspath(workspace)],
                    capture_output=True, check=False, timeout=30,
                )
            if workspace.exists():
                shutil.rmtree(workspace)
        if revision is not None and repository.exists():
            shutil.rmtree(repository)
        raise


def _checkpoint_marker_path(workspace: Path) -> Path:
    marker = workspace.parent / f".{workspace.name}-repair-checkpoint.json"
    if marker.is_file():
        return marker
    git_path = workspace / ".git"
    if git_path.is_dir():
        return git_path / "repair-checkpoint.json"
    if git_path.is_file():
        text = git_path.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            raw_admin = Path(text.partition(":")[2].strip())
            admin = (raw_admin if raw_admin.is_absolute() else workspace / raw_admin).resolve()
            return admin / "repair-checkpoint.json"
    return marker


def restore_checkpoint_workspace(workspace_root: Path, checkpoint_revision: str) -> None:
    """丢弃任务副本中的修改和生成文件，恢复到固定的基线 commit。"""
    workspace = workspace_root.expanduser().resolve()
    if not workspace.is_dir() or not checkpoint_revision:
        raise RepairToolError("任务工作区或基线 checkpoint 不存在")
    try:
        marker = json.loads(_checkpoint_marker_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RepairToolError("恢复 checkpoint 仅允许用于程序创建的任务副本") from error
    if marker != {"workspace": str(workspace), "checkpoint": checkpoint_revision}:
        raise RepairToolError("任务工作区与 checkpoint 标记不一致，已拒绝恢复")
    reset = run_process(
        ["git", "-C", os.fspath(workspace), "reset", "--hard", checkpoint_revision],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=30,
    )
    if reset.returncode != 0:
        raise RepairToolError(f"恢复 checkpoint 失败：{(reset.stderr or reset.stdout).strip()}")
    clean = run_process(
        ["git", "-C", os.fspath(workspace), "clean", "-fdx"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=30,
    )
    if clean.returncode != 0:
        raise RepairToolError(f"清理 checkpoint 外的文件失败：{(clean.stderr or clean.stdout).strip()}")


def remove_checkpoint_workspace(workspace_root: Path, checkpoint_revision: str) -> None:
    """Remove only a verified task worktree and its task-owned private Git repository."""
    workspace = workspace_root.expanduser().resolve()
    marker_path = _checkpoint_marker_path(workspace)
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RepairToolError("删除任务工作区仅允许用于程序创建的副本") from error
    if marker != {"workspace": str(workspace), "checkpoint": checkpoint_revision}:
        raise RepairToolError("任务工作区与 checkpoint 标记不一致，已拒绝删除")
    git_path = workspace / ".git"
    if git_path.is_file():
        text = git_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            raise RepairToolError("任务 worktree 的 Git 元数据无效")
        raw_admin = Path(text.partition(":")[2].strip())
        admin = (raw_admin if raw_admin.is_absolute() else workspace / raw_admin).resolve()
        common_file = admin / "commondir"
        if not admin.is_dir() or not common_file.is_file():
            raise RepairToolError("任务 worktree 缺少私有 Git 仓库")
        common = (admin / common_file.read_text(encoding="utf-8").strip()).resolve()
        expected_repository = workspace.parent / f".{workspace.name}-repository.git"
        if common != expected_repository.resolve():
            raise RepairToolError("任务 worktree 不属于任务私有 Git 仓库，已拒绝删除")
        removed = run_process(
            ["git", "-C", os.fspath(common), "worktree", "remove", "--force", os.fspath(workspace)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=30,
        )
        if removed.returncode != 0:
            raise RepairToolError(f"移除任务 worktree 失败：{(removed.stderr or removed.stdout).strip()}")
        shutil.rmtree(common)
    else:
        shutil.rmtree(workspace)
    marker_path.unlink(missing_ok=True)


def _limited_text(value: str, limit: int) -> tuple[str, bool]:
    """把外部程序输出限制在指定字符数内，并返回是否发生截断。"""
    if limit <= 0:
        raise ValueError("输出长度限制必须是正整数")
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _workspace_path(workspace_root: Path, relative_path: str, *, must_exist: bool = True) -> Path:
    """解析工作区内的相对路径，拒绝绝对路径、目录穿越和工作区外路径。"""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise RepairToolError("路径必须是非空字符串")
    root = workspace_root.expanduser().resolve()
    candidate_text = relative_path.replace("\\", "/")
    pure_path = PurePosixPath(candidate_text)
    candidate_parts = pure_path.parts
    if pure_path == PurePosixPath("."):
        candidate_parts = ()
    if pure_path.is_absolute() or (candidate_parts and ":" in candidate_parts[0]) or ".." in candidate_parts:
        raise RepairToolError("工具路径必须是工作区内的相对路径")
    candidate = (root / Path(*candidate_parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise RepairToolError("工具路径不能超出工作区") from None
    if must_exist and not candidate.exists():
        raise RepairToolError(f"路径不存在：{relative_path}")
    return candidate


def _relative_path(workspace_root: Path, path: Path) -> str:
    """把绝对路径转换成统一的工作区相对 POSIX 路径。"""
    return path.resolve().relative_to(workspace_root.resolve()).as_posix()


def read_code(
    path: str,
    *,
    workspace_root: Path,
    max_bytes: int = 1_000_000,
) -> dict[str, object]:
    """读取工作区内的一个 UTF-8 文本文件。

    参数：
        path：相对于 workspace_root 的文件路径。
        workspace_root：工具允许访问的工作区根目录。
        max_bytes：最多读取的 UTF-8 字节数，防止模型上下文被超大文件占满。
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes 必须是正整数")
    file_path = _workspace_path(workspace_root, path)
    if not file_path.is_file():
        raise RepairToolError(f"只能读取文件：{path}")
    try:
        raw = file_path.read_bytes()
    except OSError as error:
        raise RepairToolError(f"读取文件失败：{path}（{error}）") from error
    if len(raw) > max_bytes:
        raise RepairToolError(f"文件超过读取大小限制：{path}")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RepairToolError(f"文件不是有效的 UTF-8 文本：{path}") from error
    return {
        "path": _relative_path(workspace_root, file_path),
        "content": content,
        "line_count": len(content.splitlines()),
    }


def _parse_rg_output(output: str, workspace_root: Path, max_results: int) -> tuple[list[dict[str, object]], bool]:
    """解析 rg 的无标题文本输出，返回匹配项和是否达到数量上限。"""
    matches: list[dict[str, object]] = []
    for line in output.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        path = _workspace_path(workspace_root, parts[0])
        matches.append({
            "path": _relative_path(workspace_root, path),
            "line": int(parts[1]),
            "column": int(parts[2]),
            "text": parts[3],
        })
        if len(matches) >= max_results:
            return matches, True
    return matches, False


def _fallback_search(
    query: str, search_path: Path, workspace_root: Path, max_results: int,
) -> tuple[list[dict[str, object]], bool]:
    """在系统没有 rg 时逐个读取 UTF-8 文件完成文本搜索。"""
    files = [search_path] if search_path.is_file() else [
        path for path in search_path.rglob("*") if path.is_file() and ".git" not in path.parts
    ]
    matches: list[dict[str, object]] = []
    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            error_info(error, operation="code.search.skip_file")
            continue
        for line_number, line in enumerate(lines, start=1):
            column = line.find(query)
            if column < 0:
                continue
            matches.append({
                "path": _relative_path(workspace_root, file_path),
                "line": line_number,
                "column": column + 1,
                "text": line,
            })
            if len(matches) >= max_results:
                return matches, True
    return matches, False


def search_code(
    query: str,
    *,
    workspace_root: Path,
    path: str = ".",
    max_results: int = 50,
) -> dict[str, object]:
    """在工作区中搜索文本，优先使用 rg，找不到 rg 时使用 Python 回退实现。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("搜索内容不能为空")
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results <= 0:
        raise ValueError("max_results 必须是正整数")
    search_path = _workspace_path(workspace_root, path)
    rg_path = shutil.which("rg")
    if rg_path is None:
        matches, truncated = _fallback_search(query, search_path, workspace_root, max_results)
        return {"query": query, "matches": matches, "truncated": truncated, "engine": "python"}

    search_argument = "." if search_path == workspace_root.resolve() else _relative_path(workspace_root, search_path)
    completed = run_process(
        [rg_path, "--fixed-strings", "--line-number", "--column", "--no-heading", "--color", "never", "--", query, search_argument],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    if completed.returncode not in (0, 1):
        detail = completed.stderr.strip() or "rg 搜索失败"
        raise RepairToolError(detail)
    matches, truncated = _parse_rg_output(completed.stdout, workspace_root, max_results)
    if not truncated and len(matches) == max_results:
        truncated = True
    return {"query": query, "matches": matches, "truncated": truncated, "engine": "rg"}


def _validated_vector(values: Sequence[float], name: str) -> list[float]:
    """把嵌入结果转换成非空的有限浮点列表。"""
    if isinstance(values, (str, bytes)):
        raise KnowledgeBaseError(f"{name} 必须是数字序列")
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError):
        raise KnowledgeBaseError(f"{name} 必须只包含数字") from None
    if not vector or any(not math.isfinite(value) for value in vector):
        raise KnowledgeBaseError(f"{name} 必须是非空有限数字序列")
    return vector


def _vector_literal(vector: Sequence[float]) -> str:
    """把数字列表编码成 pgvector 接受的文本格式。"""
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


def _knowledge_database_dsn(env_path: Path | None = None) -> str | None:
    """从环境变量或项目 .env 读取知识库连接串，不输出连接信息。"""
    config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
    settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
    for name in ("KNOWLEDGE_DB_URL", "DATABASE_URL"):
        for value in (os.getenv(name), settings.get(name)):
            if value and value.strip():
                return value.strip()
    return None


@dataclass(frozen=True)
class PostgresKnowledgeBase:
    """使用 PostgreSQL pgvector 保存成功案例和标记为 rejected 的失败经验。"""

    dsn: str | None = None
    embedder: EmbeddingFunction = embed_text
    connection_factory: ConnectionFactory | None = None

    @classmethod
    def from_environment(cls, env_path: Path | None = None) -> PostgresKnowledgeBase | None:
        """按项目配置创建知识库；没有连接串时返回 None，允许离线任务继续运行。"""
        dsn = _knowledge_database_dsn(env_path)
        return cls(dsn=dsn) if dsn is not None else None

    def _connect(self) -> Any:
        """打开数据库连接；默认从 KNOWLEDGE_DB_URL 或 DATABASE_URL 读取连接串。"""
        dsn = self.dsn or _knowledge_database_dsn()
        if not dsn:
            raise KnowledgeBaseError("请设置 KNOWLEDGE_DB_URL 或 DATABASE_URL")
        if self.connection_factory is not None:
            return self.connection_factory(dsn)
        try:
            import psycopg
        except ImportError as error:
            raise KnowledgeBaseError("使用 PostgreSQL 知识库需要安装 psycopg[binary]") from error
        return psycopg.connect(dsn)

    def ensure_schema(self) -> None:
        """创建 pgvector 扩展和修复案例表；重复执行不会破坏已有数据。"""
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repair_cases (
                        case_id TEXT PRIMARY KEY,
                        document JSONB NOT NULL,
                        embedding vector NOT NULL,
                        dimension INTEGER NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repair_reviews (
                        task_id TEXT PRIMARY KEY,
                        decision TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        document JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repair_failures (
                        case_id TEXT PRIMARY KEY,
                        document JSONB NOT NULL,
                        embedding vector NOT NULL,
                        dimension INTEGER NOT NULL,
                        revision INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise KnowledgeBaseError(f"初始化 PostgreSQL 知识库失败：{error}") from error
        finally:
            connection.close()

    def search(
        self,
        query: str,
        limit: int = 5,
        outcome: Literal["accepted", "rejected"] = "accepted",
        filters: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """按结果类型及 JSONB 元数据过滤，用 pgvector 余弦相似度检索案例。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("知识库查询文本不能为空")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("知识库查询数量必须是正整数")
        if outcome not in ("accepted", "rejected"):
            raise ValueError("outcome 必须是 accepted 或 rejected")
        normalized_filters = _normalize_retrieval_filters(filters)
        vector = _validated_vector(self.embedder(query), "查询向量")
        self.ensure_schema()
        connection = self._connect()
        literal = _vector_literal(vector)
        table = "repair_cases" if outcome == "accepted" else "repair_failures"
        where = ["dimension = %s"]
        parameters: list[object] = [literal, len(vector)]
        if "repository_id" in normalized_filters:
            where.append("document->'retrieval_scope'->>'repository_id' = %s")
            parameters.append(normalized_filters["repository_id"])
        if "file_paths" in normalized_filters:
            where.append("document->'retrieval_scope'->'file_paths' ?| %s::text[]")
            parameters.append(normalized_filters["file_paths"])
        if "dependency_versions" in normalized_filters:
            where.append("document->'retrieval_scope'->'dependency_versions' @> %s::jsonb")
            parameters.append(json.dumps(normalized_filters["dependency_versions"], ensure_ascii=False))
        parameters.append(limit)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT case_id, revision, document,
                           1 - (embedding <=> %s::vector) AS score, '{outcome}' AS outcome
                    FROM {table}
                    WHERE {' AND '.join(where)}
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    (literal, len(vector), literal, limit),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise KnowledgeBaseError(f"检索 PostgreSQL 知识库失败：{error}") from error
        finally:
            connection.close()
        return [
            {
                "case_id": row[0],
                "revision": int(row[1]),
                "document": row[2] if isinstance(row[2], str) else json.dumps(row[2], ensure_ascii=False),
                "score": float(row[3]),
                "outcome": row[4],
            }
            for row in rows
        ]

    def save_case(self, state: RepairState) -> None:
        """保存已接受且独立验证通过的案例，并递增同一任务的版本号。"""
        document = build_case_document(state)
        search_text = _case_search_text(json.loads(document))
        vector = _validated_vector(self.embedder(search_text), "案例向量")
        self.ensure_schema()
        connection = self._connect()
        literal = _vector_literal(vector)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO repair_cases
                        (case_id, document, embedding, dimension, revision, created_at, updated_at)
                    VALUES (%s, %s::jsonb, %s::vector, %s, 1, NOW(), NOW())
                    ON CONFLICT (case_id) DO UPDATE SET
                        document = EXCLUDED.document,
                        embedding = EXCLUDED.embedding,
                        dimension = EXCLUDED.dimension,
                        revision = repair_cases.revision + 1,
                        updated_at = NOW()
                    """,
                    (state["task_id"], document, literal, len(vector)),
                )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存 PostgreSQL 修复案例失败：{error}") from error
        finally:
            connection.close()

    def record_rejection(self, state: RepairState) -> None:
        """记录用户拒绝补丁的理由；拒绝审核不写入已验收案例表。"""
        document = build_rejection_document(state)
        reason = str(state["review_reason"]).strip()
        search_text = _case_search_text(json.loads(document))
        vector = _validated_vector(self.embedder(search_text), "失败经验向量")
        literal = _vector_literal(vector)
        self.ensure_schema()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO repair_reviews (task_id, decision, reason, document, created_at)
                    VALUES (%s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (task_id) DO UPDATE SET
                        decision = EXCLUDED.decision,
                        reason = EXCLUDED.reason,
                        document = EXCLUDED.document,
                        created_at = NOW()
                    """,
                    (state["task_id"], "rejected", reason, document),
                )
                cursor.execute(
                    """
                    INSERT INTO repair_failures
                        (case_id, document, embedding, dimension, revision, created_at, updated_at)
                    VALUES (%s, %s::jsonb, %s::vector, %s, 1, NOW(), NOW())
                    ON CONFLICT (case_id) DO UPDATE SET
                        document = EXCLUDED.document,
                        embedding = EXCLUDED.embedding,
                        dimension = EXCLUDED.dimension,
                        revision = repair_failures.revision + 1,
                        updated_at = NOW()
                    """,
                    (state["task_id"], document, literal, len(vector)),
                )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存 PostgreSQL 拒绝审核记录失败：{error}") from error
        finally:
            connection.close()


def retrieve_repair_experience(
    query: str,
    *,
    knowledge_base: Any,
    limit: int = 5,
    outcome: Literal["accepted", "rejected"] = "accepted",
    filters: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """查询知识库并补充案例正文中记录的适用条件。"""
    if knowledge_base is None:
        raise RepairToolError("尚未配置修复经验知识库")
    results = knowledge_base.search(query, limit=limit, outcome=outcome, filters=filters)
    normalized: list[dict[str, object]] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        item = dict(result)
        document = item.get("document")
        if isinstance(document, str):
            try:
                document = json.loads(document)
            except json.JSONDecodeError:
                document = {}
        conditions = document.get("applicable_conditions", []) if isinstance(document, Mapping) else []
        item["applicable_conditions"] = conditions if isinstance(conditions, list) else []
        # 向补丁模型提供修复要点及引用来源，限制历史日志占用上下文。
        if isinstance(document, Mapping):
            item["document"] = {
                key: document.get(key) for key in (
                    "original_report", "diagnosis", "patch_diff", "source", "applicable_conditions",
                    "review_reason",
                )
            }
            for key, maximum in (("original_report", 2000), ("diagnosis", 3000), ("patch_diff", 6000)):
                if isinstance(item["document"].get(key), str):
                    item["document"][key] = item["document"][key][:maximum]
        item["outcome"] = outcome
        if outcome == "accepted":
            item.update(decision="pending", matched_conditions=[], conflicts=[], unknowns=[])
        normalized.append(item)
    return normalized


def save_repair_experience(state: RepairState, *, knowledge_base: Any) -> None:
    """把已接受且验证通过的状态交给知识库写入工具。"""
    if knowledge_base is None:
        raise RepairToolError("尚未配置修复经验知识库")
    knowledge_base.save_case(state)


_DIFF_HEADER = re.compile(r"^(---|\+\+\+) ([^\t]+)(?:\t.*)?$")


def _diff_paths(patch_diff: str) -> set[str]:
    """提取 unified diff 中涉及的工作区相对路径。"""
    paths: set[str] = set()
    for line in patch_diff.splitlines():
        match = _DIFF_HEADER.match(line)
        if match is None:
            continue
        path = match.group(2)
        if path == "/dev/null":
            continue
        for prefix in ("a/", "b/"):
            if path.startswith(prefix):
                path = path[2:]
                break
        # _workspace_path() also rejects traversal and absolute paths.
        paths.add(path)
    if not paths:
        raise RepairToolError("补丁中没有可识别的 unified diff 文件路径")
    return paths


def _check_patch_scope(
    patch_diff: str,
    workspace_root: Path,
    allowed_paths: Sequence[str],
    protected_test_targets: Sequence[str] = (),
    protected_test_files: Sequence[str] = (),
) -> set[str]:
    """确保补丁涉及的每个文件都在调用方明确允许的范围内。"""
    if not allowed_paths:
        raise RepairToolError("应用补丁前必须提供允许修改的路径范围")
    root = workspace_root.resolve()
    allowed = [_workspace_path(root, item, must_exist=False) for item in allowed_paths]
    protected_tests = []
    for test_target in protected_test_targets:
        file_part = test_target.split("::", 1)[0].strip()
        if PurePosixPath(file_part.replace("\\", "/")) == PurePosixPath("."):
            continue
        protected_tests.append(_workspace_path(root, file_part, must_exist=False))
    protected_tests.extend(
        _workspace_path(root, file_part, must_exist=False) for file_part in protected_test_files
    )
    touched = _diff_paths(patch_diff)
    for relative in touched:
        parts = PurePosixPath(relative.replace("\\", "/")).parts
        name = parts[-1].lower()
        if (any(part.lower() in (".git", "tests", "test") for part in parts)
                or name.startswith("test_") or name.endswith("_test.py")
                or name in ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")):
            raise RepairToolError("补丁不能修改测试文件、测试配置或 Git 元数据")
        target = _workspace_path(root, relative, must_exist=False)
        if any(target == protected or protected in target.parents for protected in protected_tests):
            raise RepairToolError("补丁不能修改任务配置的测试目标")
        if not any(target == permitted or permitted in target.parents for permitted in allowed):
            raise RepairToolError(f"补丁超出允许修改范围：{relative}")
    return touched


def _patch_input_bytes(patch_diff: str, root: Path) -> bytes:
    """用二进制输入传递补丁，并保留每个原文件的 LF 或 CRLF 换行。"""
    lines = []
    newline = "\n"
    for line in patch_diff.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        header = _DIFF_HEADER.match(content)
        if header is not None and header.group(1) == "---":
            relative = header.group(2)
            if relative.startswith(("a/", "b/")):
                relative = relative[2:]
            newline = "\n"
            if relative != "/dev/null":
                source = _workspace_path(root, relative, must_exist=False)
                if source.is_file():
                    with source.open("rb") as stream:
                        first_line = stream.readline()
                    if first_line.endswith(b"\r\n"):
                        newline = "\r\n"
        lines.append(content + newline if line.endswith("\n") else content)
    return "".join(lines).encode("utf-8")


def apply_patch(
    patch_diff: str,
    *,
    workspace_root: Path,
    allowed_paths: Sequence[str],
    protected_test_targets: Sequence[str] = (),
    protected_test_files: Sequence[str] = (),
    max_output_chars: int = 20_000,
) -> dict[str, object]:
    """检查范围后用 git apply --check 和 git apply 应用 unified diff。"""
    if not isinstance(patch_diff, str) or not patch_diff.strip():
        raise ValueError("补丁内容不能为空")
    touched = _check_patch_scope(
        patch_diff, workspace_root, allowed_paths, protected_test_targets, protected_test_files,
    )
    root = workspace_root.resolve()
    if not (root / ".git").exists():
        raise RepairToolError("应用补丁需要工作区包含 .git")
    command = ["git", "apply", "--whitespace=error"]
    patch_bytes = _patch_input_bytes(patch_diff, root)
    parsed = run_process(["git", "apply", "--numstat", "-z", "-"], cwd=root, input=patch_bytes,
                         capture_output=True, check=False, timeout=10)
    actual_paths = set()
    for record in parsed.stdout.decode("utf-8", errors="replace").split("\0"):
        if record:
            fields = record.split("\t", 2)
            if len(fields) != 3 or not fields[2]:
                raise RepairToolError("不支持重命名、复制或无法解析路径的补丁")
            actual_paths.add(fields[2])
    if parsed.returncode or actual_paths != touched:
        raise RepairToolError("Git 解析的补丁路径与声明不一致，拒绝应用")
    check = run_process(
        [*command, "--check", "-"], cwd=root, input=patch_bytes, capture_output=True, check=False, timeout=30,
    )
    if check.returncode != 0:
        detail, _ = _limited_text((check.stderr or check.stdout).decode("utf-8", errors="replace"), max_output_chars)
        raise RepairToolError(f"补丁检查失败：{detail.strip()}")
    applied = run_process(
        [*command, "-"], cwd=root, input=patch_bytes, capture_output=True, check=False, timeout=30,
    )
    if applied.returncode != 0:
        detail, _ = _limited_text((applied.stderr or applied.stdout).decode("utf-8", errors="replace"), max_output_chars)
        raise RepairToolError(f"补丁应用失败：{detail.strip()}")
    output, truncated = _limited_text(applied.stdout.decode("utf-8", errors="replace"), max_output_chars)
    return {"paths": sorted(touched), "return_code": applied.returncode, "output": output, "truncated": truncated}


def collect_test_files(
    *,
    workspace_root: Path,
    test_targets: Sequence[str],
    timeout_seconds: float = 300.0,
    execution_backend: str | None = None,
    task_id: str | None = None,
) -> tuple[str, ...]:
    """在基线 checkpoint 收集 pytest 用例，并返回包含测试的工作区相对路径。"""
    if not test_targets:
        raise RepairToolError("必须提供固定 pytest 目标，才能保护验收测试")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须是正数")
    root = workspace_root.resolve()
    for target in test_targets:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("测试目标必须是非空字符串")
        _workspace_path(root, target.split("::", 1)[0])
    backend = get_execution_backend(execution_backend)
    command = (
        ["python", "-I", "-B", "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider",
         "--rootdir=/workspace", "--collect-only", "-q", "--color=no", *test_targets]
        if backend == "docker" else
        [os.fspath(sys.executable), "-B", "-m", "pytest", "-p", "no:cacheprovider",
         "--collect-only", "-q", "--color=no", *test_targets]
    )
    test_env = os.environ.copy()
    for name in (
        "API_KEY", "CHAT_API_KEY", "EMBEDDING_API_KEY", "BASE_URL",
        "CHAT_BASE_URL", "EMBEDDING_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        test_env.pop(name, None)
    max_output_bytes = 5_000_000
    with tempfile.TemporaryFile(mode="w+b") as stdout_stream, tempfile.TemporaryFile(mode="w+b") as stderr_stream:
        try:
            completed = run_execution(
                command, workspace=root, backend=backend, host_env=test_env,
                timeout=float(timeout_seconds), task_id=task_id,
                stdout=stdout_stream, stderr=stderr_stream,
            )
        except subprocess.TimeoutExpired as error:
            raise RepairToolError("pytest 基线收集超时，无法安全锁定验收测试") from error
        stdout_stream.seek(0)
        output_bytes = stdout_stream.read(max_output_bytes + 1)
    if len(output_bytes) > max_output_bytes:
        raise RepairToolError("pytest 基线收集输出过大，无法安全锁定验收测试")
    output = output_bytes.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise RepairToolError(
            f"pytest 基线收集失败（退出码 {completed.returncode}），拒绝开放补丁和测试工具"
        )

    collected: set[str] = set()
    for line in output.splitlines():
        item = line.strip()
        if "::" not in item:
            continue
        raw_path = item.split("::", 1)[0].strip()
        candidate = Path(raw_path)
        try:
            if candidate.is_absolute():
                relative = candidate.resolve().relative_to(root).as_posix()
            else:
                relative = candidate.as_posix()
            resolved = _workspace_path(root, relative)
        except (OSError, ValueError, RepairToolError):
            raise RepairToolError("pytest 返回了无法验证的测试路径，拒绝开放补丁和测试工具") from None
        if not resolved.is_file():
            raise RepairToolError("pytest 收集的测试文件不存在，拒绝开放补丁和测试工具")
        collected.add(resolved.relative_to(root).as_posix())
    if not collected:
        raise RepairToolError("pytest 没有返回可锁定的测试文件，拒绝开放补丁和测试工具")
    return tuple(sorted(collected))


def run_tests(
    *,
    workspace_root: Path,
    test_targets: Sequence[str],
    timeout_seconds: float = 300.0,
    max_output_chars: int = 30_000,
    junit_report: str | None = None,
    execution_backend: str | None = None,
    task_id: str | None = None,
    protected_test_files: Sequence[str] = (),
) -> dict[str, object]:
    """在隔离工作区中运行 pytest；默认使用 Docker，返回真实退出码和输出。"""
    if not test_targets:
        raise ValueError("必须提供至少一个 pytest 测试目标")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须是正数")
    root = workspace_root.resolve()
    for target in test_targets:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("测试目标必须是非空字符串")
        # 文件目标必须在工作区内；pytest node id 中的 :: 之后保留给 pytest 解析。
        file_part = target.split("::", 1)[0]
        _workspace_path(root, file_part)
    backend = get_execution_backend(execution_backend)
    command = (["python", "-I", "-B", "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider",
                "--rootdir=/workspace", *test_targets] if backend == "docker" else
               [os.fspath(sys.executable), "-B", "-m", "pytest", *test_targets])
    if junit_report is not None:
        report = _workspace_path(root, junit_report, must_exist=False)
        report_target = f"/workspace/{report.relative_to(root).as_posix()}" if backend == "docker" else str(report)
        command.append(f"--junitxml={report_target}")
    test_env = os.environ.copy()
    for name in (
        "API_KEY", "CHAT_API_KEY", "EMBEDDING_API_KEY", "BASE_URL",
        "CHAT_BASE_URL", "EMBEDDING_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        test_env.pop(name, None)
    extra_mounts: list[BindMount] = []
    if backend == "docker":
        for test_file in protected_test_files:
            protected = _workspace_path(root, test_file)
            extra_mounts.append(BindMount(protected, f"/workspace/{protected.relative_to(root).as_posix()}", True))
    def read_output(stream) -> tuple[str, bool]:
        stream.seek(0)
        content = stream.read(max_output_chars + 1)
        text = content.decode("utf-8", errors="replace")
        return _limited_text(text, max_output_chars)

    with tempfile.TemporaryFile(mode="w+b") as stdout_stream, tempfile.TemporaryFile(mode="w+b") as stderr_stream:
        try:
            completed = run_execution(
                command, workspace=root, backend=backend, host_env=test_env,
                timeout=float(timeout_seconds), task_id=task_id,
                extra_mounts=tuple(extra_mounts),
                stdout=stdout_stream, stderr=stderr_stream,
            )
        except subprocess.TimeoutExpired as error:
            stdout, stdout_truncated = read_output(stdout_stream)
            stderr, stderr_truncated = read_output(stderr_stream)
            return {
                "command": command,
                "return_code": None,
                "passed": False,
                "timed_out": True,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
                "error_info": error_info(error, operation="tests.run"),
            }
        stdout, stdout_truncated = read_output(stdout_stream)
        stderr, stderr_truncated = read_output(stderr_stream)
    result = {
        "command": command,
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "timed_out": False,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
    }
    if completed.returncode != 0:
        code, message = {1: ("tests_failed", "测试断言未通过"), 2: ("test_collection_failed", "测试收集失败或被中断"),
                         3: ("test_runner_failed", "pytest 内部错误"), 4: ("test_configuration_invalid", "pytest 参数或配置错误"),
                         5: ("tests_not_collected", "没有收集到测试")}.get(completed.returncode, ("test_process_crashed", "测试进程异常退出"))
        result["error_info"] = error_info(Fault(code, message, category="business" if completed.returncode == 1 else "execution",
            next_action="查看测试日志并修复失败用例" if completed.returncode == 1 else "检查测试目标、依赖、配置和运行环境"),
            operation="tests.run", details={"return_code": completed.returncode})
    return result


TOOL_DESCRIPTIONS = {
    "read_code": "读取工作区内指定的 UTF-8 代码文件。",
    "search_code": "在工作区中搜索文本并返回文件、行号、列号和匹配行。",
    "retrieve_repair_experience": "按 outcome 分开检索已验收案例或被拒绝的失败经验，并可按已读取文件路径缩小范围。",
    "apply_patch": "从任务 checkpoint 开始应用完整补丁；程序会检查修改路径并禁止改动测试。",
    "run_tests": "运行任务预设的 pytest 目标；模型不能更改测试命令或范围。",
}

TOOL_DEFINITIONS: tuple[dict[str, object], ...] = tuple(
    {
        "type": "function",
        "name": name,
        "description": TOOL_DESCRIPTIONS[name],
        "parameters": arguments_model.model_json_schema(),
    }
    for name, arguments_model in TOOL_ARGUMENTS.items()
)


@dataclass
class RepairToolbox:
    """绑定工作区和外部知识库的工具集合。"""

    workspace_root: Path
    knowledge_base: PostgresKnowledgeBase | Any | None = None
    retrieval_scope: Mapping[str, object] = field(default_factory=dict)
    allowed_paths: tuple[str, ...] = ()
    test_targets: tuple[str, ...] = ()
    test_timeout_seconds: float = 300.0
    checkpoint_revision: str | None = None
    source_root: Path | None = None
    validation_report_path: Path | None = None
    execution_backend: str = field(default_factory=get_execution_backend)
    task_id: str | None = None
    permitted_model_tools: tuple[str, ...] = (
        "read_code", "search_code", "retrieve_repair_experience", "apply_patch", "run_tests",
    )
    mcp_client: MCPToolClient = field(default_factory=MCPToolClient, repr=False)
    protected_test_hashes: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _regression_case: dict[str, object] | None = field(default=None, init=False, repr=False)
    _definitions: tuple[dict[str, object], ...] = field(default=TOOL_DEFINITIONS, init=False, repr=False)

    def model_tool_definitions(self) -> list[dict[str, object]]:
        """返回本地工具和 MCP allowlist 中可交给模型的工具定义。"""
        local = [dict(definition) for definition in self._definitions
                 if definition["name"] in self.permitted_model_tools and definition["name"] in MODEL_TOOL_ALLOWLIST]
        return local + self.mcp_client.start()

    def close_mcp_client(self) -> None:
        """释放本次补丁提议使用的 MCP 会话。"""
        self.mcp_client.close()

    def read_code(self, path: str, max_bytes: int = 1_000_000) -> dict[str, object]:
        """调用工作区文件读取工具。"""
        return read_code(path, workspace_root=self.workspace_root, max_bytes=max_bytes)

    def search_code(self, query: str, path: str = ".", max_results: int = 50) -> dict[str, object]:
        """调用工作区文本搜索工具。"""
        return search_code(query, workspace_root=self.workspace_root, path=path, max_results=max_results)

    def retrieve_repair_experience(
        self,
        query: str,
        limit: int = 5,
        outcome: Literal["accepted", "rejected"] = "accepted",
        file_paths: Sequence[str] = (),
        *,
        filters: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """使用任务来源过滤条件检索指定类型的经验，可再按已读取文件收窄。"""
        effective_filters = dict(filters if filters is not None else self.retrieval_scope)
        if file_paths:
            requested = set()
            for path in file_paths:
                target = _workspace_path(self.workspace_root, path)
                if not target.is_file():
                    raise RepairToolError("检索过滤路径必须指向工作区内的现有文件")
                requested.add(_relative_path(self.workspace_root, target).casefold())
            current_paths = set(effective_filters.get("file_paths", []))
            effective_filters["file_paths"] = sorted(requested & current_paths) if current_paths else sorted(requested)
        return retrieve_repair_experience(
            query, knowledge_base=self.knowledge_base, limit=limit, outcome=outcome, filters=effective_filters,
        )

    def _ensure_test_protection(self, remaining_seconds: float | None = None) -> None:
        """在基线上收集 pytest 用例，并固定每个测试文件的内容哈希。"""
        if self.checkpoint_revision is None:
            return
        if self.protected_test_hashes:
            self._verify_test_protection()
            return
        timeout = self.test_timeout_seconds
        if remaining_seconds is not None:
            if remaining_seconds <= 0:
                raise RepairToolError("自动运行时间预算已耗尽，无法锁定验收测试")
            timeout = min(timeout, remaining_seconds)
        files = collect_test_files(
            workspace_root=self.workspace_root,
            test_targets=self.test_targets,
            timeout_seconds=timeout,
            execution_backend=self.execution_backend,
            task_id=self.task_id,
        )
        root = self.workspace_root.resolve()
        self.protected_test_hashes = {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in files
        }
        trace("tests.protection", "locked", details={"file_count": len(files)})

    def _verify_test_protection(self) -> None:
        """发现基线测试文件被执行代码修改时拒绝继续，并恢复任务副本。"""
        root = self.workspace_root.resolve()
        changed = []
        for relative, expected in self.protected_test_hashes.items():
            path = root / relative
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual != expected:
                changed.append(relative)
        if changed:
            if self.checkpoint_revision is not None:
                with cleanup_scope():
                    restore_checkpoint_workspace(self.workspace_root, self.checkpoint_revision)
            raise RepairToolError("固定验收测试文件发生变化，任务副本已恢复到 checkpoint")

    def apply_patch(self, patch_diff: str, remaining_seconds: float | None = None) -> dict[str, object]:
        """在任务工作区应用补丁，并检查调用方允许修改的路径。"""
        started = time.monotonic()
        self._ensure_test_protection(remaining_seconds)
        if remaining_seconds is not None and remaining_seconds - (time.monotonic() - started) <= 0:
            raise RepairToolError("自动运行时间预算已耗尽，不能应用补丁")
        return apply_patch(
            patch_diff,
            workspace_root=self.workspace_root,
            allowed_paths=self.allowed_paths,
            protected_test_targets=self.test_targets,
            protected_test_files=tuple(self.protected_test_hashes),
        )

    def run_tests(self, remaining_seconds: float | None = None) -> dict[str, object]:
        """运行固定测试目标，并把超时限制在当前预算内。"""
        started = time.monotonic()
        self._ensure_test_protection(remaining_seconds)
        timeout_seconds = self.test_timeout_seconds
        if remaining_seconds is not None:
            timeout_seconds = min(timeout_seconds, remaining_seconds - (time.monotonic() - started))
            if timeout_seconds <= 0:
                raise RepairToolError("自动运行时间预算已耗尽，无法运行测试")
        try:
            result = run_tests(
                workspace_root=self.workspace_root,
                test_targets=self.test_targets,
                timeout_seconds=timeout_seconds,
                execution_backend=self.execution_backend,
                task_id=self.task_id,
                protected_test_files=tuple(self.protected_test_hashes),
            )
        finally:
            self._verify_test_protection()
        return result

    def apply_model_patch(self, patch_diff: str, remaining_seconds: float | None = None) -> dict[str, object]:
        """从任务 checkpoint 开始应用模型的完整补丁，避免叠加上一版试验。"""
        if self.checkpoint_revision is None:
            raise RepairToolError("模型应用补丁需要任务基线 checkpoint")
        started = time.monotonic()
        with cleanup_scope():
            restore_checkpoint_workspace(self.workspace_root, self.checkpoint_revision)
        remaining = None if remaining_seconds is None else remaining_seconds - (time.monotonic() - started)
        return self.apply_patch(patch_diff, remaining_seconds=remaining)

    def save_repair_experience(self, state: RepairState) -> None:
        """由固定工作流在人工接受后保存修复经验。"""
        save_repair_experience(state, knowledge_base=self.knowledge_base)

    def record_rejection(self, state: RepairState) -> None:
        """把拒绝理由交给配置的知识库审核记录表。"""
        if self.knowledge_base is None:
            raise RepairToolError("尚未配置修复经验知识库，无法记录拒绝理由")
        recorder = getattr(self.knowledge_base, "record_rejection", None)
        if not callable(recorder):
            raise RepairToolError("当前知识库不支持记录拒绝理由")
        recorder(state)

    def restore_workspace(self, state: RepairState) -> None:
        """恢复本任务的隔离工作区，不触碰用户提供的原始目录。"""
        if self.checkpoint_revision is None:
            raise RepairToolError("当前任务没有基线 checkpoint")
        with traced_call("checkpoint.restore", parameters={"checkpoint": self.checkpoint_revision}):
            with cleanup_scope():
                restore_checkpoint_workspace(self.workspace_root, self.checkpoint_revision)

    def collect_context_for_state(self, state: RepairState) -> dict[str, object]:
        """为工作流收集源码证据并检索原始报告相关的历史案例。"""
        evidence: list[dict[str, object]] = []
        source = state["source"]
        source_type = source.get("type")
        if state["code_text"] is not None:
            evidence.append({
                "path": "<pasted_code>",
                "start_line": 1,
                "end_line": max(1, len(state["code_text"].splitlines())),
                "quote": state["code_text"],
                "reason": "任务直接提供的源码",
            })
        elif source_type == "python_file" and isinstance(source.get("path"), str):
            source_path = Path(str(source["path"])).resolve()
            try:
                relative_root = (self.source_root or self.workspace_root).expanduser().resolve()
                relative = source_path.relative_to(relative_root).as_posix()
                file_data = self.read_code(relative)
                evidence.append({
                    "path": file_data["path"],
                    "start_line": 1,
                    "end_line": max(1, int(file_data["line_count"])),
                    "quote": file_data["content"],
                    "reason": "任务指定的 Python 源码",
                })
            except (ValueError, RepairToolError) as error:
                error_info(error, operation="context.read_code")
                # 外部路径不属于工具工作区时不读取，交给调用方明确配置工作区。
                pass
        matches: list[dict[str, object]] = []
        if self.knowledge_base is not None:
            query = [state["original_report"]]
            reproduction = state.get("reproduction") or {}
            for key in ("revised_report", "steps"):
                if isinstance(reproduction.get(key), str):
                    query.append(reproduction[key][:2000])
            execution = reproduction.get("execution")
            if isinstance(execution, Mapping):
                query.extend(str(execution.get(key) or "")[:2000] for key in ("stdout", "stderr"))
            source_scope = source.get("retrieval_scope", {})
            filters = source_scope if isinstance(source_scope, Mapping) else {}
            query_text = "\n".join(query)[:10_000]
            matches = self.retrieve_repair_experience(query_text, outcome="accepted", filters=filters)
            failures = self.retrieve_repair_experience(query_text, outcome="rejected", filters=filters)
        return {"code_evidence": evidence, "case_matches": matches, "failure_matches": failures}

    def apply_patch_for_state(self, state: RepairState) -> None:
        """把状态中的候选补丁交给范围检查和 Git 应用工具。"""
        patch_diff = state["patch_diff"]
        if not isinstance(patch_diff, str):
            raise RepairToolError("状态中没有可应用的补丁")
        self.apply_patch(patch_diff, remaining_seconds=float(state["remaining_budget"]["seconds"]))

    def _validation_directory(self) -> Path:
        """把固定用例和运行日志保存在补丁工作区之外，重试恢复不会删除它们。"""
        if self.validation_report_path is not None:
            directory = self.validation_report_path.resolve().parent
            if directory.is_relative_to(self.workspace_root.resolve()):
                raise RepairToolError("验证报告和固定回归用例必须保存在补丁工作区之外")
            return directory
        root = self.workspace_root.resolve()
        return root.parent / f"{root.name}-validation"

    def _run_regression_test(self, code: str, timeout_seconds: float | None = None) -> dict[str, object]:
        """显式执行固定用例，并核对 JUnit 结果以排除跳过、收集错误及空运行。"""
        root = self.workspace_root.resolve()
        with tempfile.TemporaryDirectory(prefix=".repair-regression-", dir=root) as directory:
            test_dir = Path(directory)
            test_file = test_dir / "test_reported_defect.py"
            test_file.write_text(code, encoding="utf-8")
            junit_file = test_dir / "result.xml"
            result = run_tests(
                workspace_root=root,
                test_targets=(test_file.relative_to(root).as_posix(),),
                timeout_seconds=self.test_timeout_seconds if timeout_seconds is None else timeout_seconds,
                junit_report=junit_file.relative_to(root).as_posix(),
                execution_backend=self.execution_backend,
                task_id=self.task_id,
            )
            try:
                cases = ET.parse(junit_file).getroot().findall(".//testcase")
                failures = [node for case in cases for node in case.findall("failure")]
                errors = sum(len(case.findall("error")) for case in cases)
                skipped = sum(len(case.findall("skipped")) for case in cases)
                dependency_error = any(
                    node.get("message", "").startswith(("ImportError:", "ModuleNotFoundError:", "SyntaxError:"))
                    for node in failures
                )
                valid = len(cases) == 1 and not errors and not skipped and not dependency_error
                valid = valid and (
                    (result["return_code"] == 0 and not failures)
                    or (result["return_code"] == 1 and len(failures) == 1)
                )
                result.update(collected=len(cases), failures=len(failures), errors=errors, skipped=skipped)
            except (OSError, ET.ParseError) as error:
                valid = False
                result["report_error"] = error_info(error, operation="tests.junit.read")
            result["valid"] = valid
            result["passed"] = bool(valid and result["passed"])
            if not valid and not result.get("report_error"):
                result["report_error"] = error_info(Fault("regression_result_invalid", "缺陷用例运行结果不满足验收条件", category="validation"), operation="tests.junit.validate")
            return result

    def prepare_regression_for_state(
        self, state: RepairState, record: Mapping[str, object],
    ) -> dict[str, object]:
        """补丁生成前固定专用用例，只有它在原实现上真实失败才允许继续。"""
        self._regression_case = None
        started = time.monotonic()
        reproduction_code = record.get("reproduction_code")
        if not isinstance(reproduction_code, str) or not reproduction_code.strip():
            raise RepairToolError("缺少已确认的原复现脚本，不能生成缺陷回归用例")
        generated = generate_regression_test(state["original_report"], record, state["code_text"])
        if generated.get("question"):
            return generated
        code = str(generated["test_code"])
        directory = self._validation_directory() / "regression"
        directory.mkdir(parents=True, exist_ok=True)
        test_path = directory / "test_reported_defect.py"
        test_path.write_text(code, encoding="utf-8")
        timeout = max(0.001, min(
            self.test_timeout_seconds,
            float(state["remaining_budget"]["seconds"]) - (time.monotonic() - started),
        ))
        baseline = self._observe_validation("regression.baseline", state,
            lambda: self._run_regression_test(code, timeout), expected_return_code=1)
        regression: dict[str, object] = {
            "test_code": code,
            "test_path": str(test_path),
            "test_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "baseline": baseline,
        }
        generated["regression"] = regression
        if not baseline["valid"] or baseline["return_code"] != 1:
            generated["question"] = "生成的缺陷回归用例未在基线上产生有效失败，请确认触发条件和预期行为。"
        else:
            self._regression_case = {**regression, "reproduction_code": reproduction_code}
        return generated

    def _observe_validation(self, name, state, action, *, expected_return_code=0, matches=None):
        with traced_call("validation.check") as call:
            call["details"] = {"check": name, "patch_hash": state.get("patch_hash"),
                               "expected_return_code": expected_return_code}
            result = action()
            call["result"] = result
            code = result.get("return_code", result.get("returncode"))
            call["details"]["matched"] = (matches(result) if matches else
                code == expected_return_code and not result.get("timed_out") and result.get("valid", True))
            return result

    def validate_patch_for_state(self, state: RepairState) -> dict[str, object]:
        """同时验证现有测试、基线失败的固定用例以及原复现脚本。"""
        deadline = time.monotonic() + float(state["remaining_budget"]["seconds"])

        def remaining_timeout() -> float:
            return max(0.001, min(self.test_timeout_seconds, deadline - time.monotonic()))

        result = self._observe_validation("existing_tests", state, lambda: self.run_tests(remaining_timeout()))
        checks = {"现有测试": result}
        regression = self._regression_case
        patched = None
        reproduction = None
        if regression is None:
            passed = None
            summary = "缺少已在基线上失败的缺陷回归用例，无法确认原缺陷已修复"
            trace("validation.check", "skipped", details={"check": "regression.patched", "reason": "missing_baseline_regression"})
            trace("validation.check", "skipped", details={"check": "reproduction.patched", "reason": "missing_baseline_regression"})
        else:
            patched = self._observe_validation("regression.patched", state,
                lambda: self._run_regression_test(str(regression["test_code"]), remaining_timeout()))
            checks["缺陷回归用例：基线"] = regression["baseline"]
            checks["缺陷回归用例：补丁后"] = patched
            reproduction_dir = self._validation_directory() / "post-reproduction"
            reproduction_dir.mkdir(parents=True, exist_ok=True)
            reproduction = self._observe_validation("reproduction.patched", state, lambda: execute_reproduction(
                str(regression["reproduction_code"]), self.workspace_root.resolve(), reproduction_dir,
                remaining_timeout(), execution_backend=self.execution_backend, task_id=self.task_id,
            ), matches=lambda execution: reproduction_exit_matches_test(str(regression["test_code"]), execution))
            reproduced_ok = reproduction_exit_matches_test(str(regression["test_code"]), reproduction)
            checks["原复现脚本：补丁后"] = {**reproduction, "passed": reproduced_ok}
            passed = bool(result["passed"] and patched["passed"] and reproduced_ok)
            failed_checks = [name for name, check in checks.items() if name != "缺陷回归用例：基线" and not check["passed"]]
            summary = "；".join(f"{name}未通过" for name in failed_checks) if failed_checks else "现有测试、缺陷回归用例及原复现脚本均通过"
        timed_out = any(check.get("timed_out") for check in checks.values())
        if timed_out:
            summary = "补丁验证超时"
        report_path = self.validation_report_path or self._validation_directory() / "validation.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# 验证报告\n\n"
            f"- 补丁哈希：{state['patch_hash']}\n"
            f"- 是否通过：{passed}\n- 摘要：{summary}\n"
            + (f"- 缺陷回归用例：{regression['test_path']}\n"
               f"- 用例哈希：{regression['test_hash']}\n" if regression else "")
            + "\n".join(
                f"\n## {name}\n\n{fenced(json.dumps(check, ensure_ascii=False, indent=2), 'json')}\n"
                for name, check in checks.items()
            ),
            encoding="utf-8",
        )
        return {
            "patch_hash": state["patch_hash"] or "",
            "passed": passed,
            "summary": summary,
            "report_path": str(report_path),
            "return_code": result["return_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "timed_out": timed_out,
            "regression": {**regression, "patched": patched} if regression else None,
            "reproduction": reproduction,
            "failure_errors": [check["error_info"] for name, check in checks.items()
                               if name != "缺陷回归用例：基线" and check.get("error_info") and not check["passed"]],
        }

    def as_repair_services(
        self,
        reproduce: Callable[[RepairState], Mapping[str, object]],
        propose_patch: Callable[[RepairState], Mapping[str, object]] | None = None,
    ):
        """把工具箱适配为当前 LangGraph RepairServices 所需的固定节点回调。"""
        # 局部导入避免 repair_workflow 导入 llm_client 时形成模块循环依赖。
        from repair_workflow import RepairServices, llm_propose_patch

        def reproduce_with_regression(state: RepairState) -> Mapping[str, object]:
            result = dict(reproduce(state))
            if result.get("matched") is True and not result.get("question"):
                record = dict(result["record"])
                generated = self.prepare_regression_for_state(state, record)
                if "regression" in generated:
                    record["regression"] = generated["regression"]
                result["record"] = record
                for key in ("question", "token_usage", "estimated_cost"):
                    if key in generated:
                        result[key] = generated[key]
            return result

        def propose_with_tools(state: RepairState) -> Mapping[str, object]:
            """模型可试用补丁并运行固定测试；试验结束后恢复工作区供固定节点验收。"""
            started = time.monotonic()
            used_side_effect_tool = False

            def execute_tool(name: str, arguments: Mapping[str, object]) -> object:
                """执行已授权工具，并把本次 ReAct 剩余时间预算传给补丁和测试工具。"""
                nonlocal used_side_effect_tool
                if name in ("apply_patch", "run_tests") or name.startswith("mcp__"):
                    used_side_effect_tool = True
                remaining = float(state["remaining_budget"]["seconds"]) - (time.monotonic() - started)
                return self.call_model_tool(name, arguments, remaining_seconds=remaining)

            try:
                definitions = self.model_tool_definitions()
                return llm_propose_patch(
                    state,
                    tool_definitions=definitions,
                    tool_executor=execute_tool,
                    additional_tool_names=tuple(self.mcp_client.tool_names),
                )
            finally:
                try:
                    if used_side_effect_tool and self.checkpoint_revision is not None:
                        self.restore_workspace(state)
                finally:
                    self.close_mcp_client()

        return RepairServices(
            reproduce=reproduce_with_regression,
            collect_context=self.collect_context_for_state,
            propose_patch=propose_patch or propose_with_tools,
            apply_patch=self.apply_patch_for_state,
            validate_patch=self.validate_patch_for_state,
            save_case=self.save_repair_experience,
            record_rejection=self.record_rejection,
            restore_checkpoint=self.restore_workspace,
        )

    def call_model_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        remaining_seconds: float | None = None,
    ) -> object:
        """校验并执行模型工具，成对记录参数、观察结果或失败。"""
        external_tool = name.startswith("mcp__")
        trace_arguments = arguments
        if external_tool:
            digest = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            trace_arguments = {"arguments_sha256": digest}
        with traced_call(name, parameters=trace_arguments, input_data=trace_arguments, tool_name=name) as call:
            result = self._call_model_tool(name, arguments, remaining_seconds=remaining_seconds)
            call["result"] = result
            return result

    def _call_model_tool(self, name, arguments, *, remaining_seconds=None):
        """先执行服务端权限校验，再校验参数并分派到固定工具实现。"""
        if remaining_seconds is not None and remaining_seconds <= 0:
            raise RepairToolError("自动运行时间预算已耗尽，无法执行模型请求的工具")
        if name not in MODEL_TOOL_ALLOWLIST:
            if name not in self.mcp_client.tool_names:
                raise RepairToolError(f"模型不能直接调用工具：{name}")
            trace("mcp.tool", "authorized", decision="explicit_server_allowlist", details={"model_tool": name})
            return self.mcp_client.call_tool(name, arguments, timeout_seconds=remaining_seconds)
        if name not in self.permitted_model_tools:
            raise RepairToolError(f"模型不能直接调用工具：{name}")
        try:
            arguments = TOOL_ARGUMENTS[name].model_validate(dict(arguments)).model_dump()
        except (ValidationError, TypeError, ValueError):
            raise RepairToolError("工具参数无效（Schema 校验失败）：请修正字段名称、类型和取值范围") from None
        trace(name, "authorized", parameters=arguments, decision="server_allowlist")
        if name == "read_code":
            return self.read_code(arguments["path"])  # type: ignore[arg-type]
        if name == "search_code":
            return self.search_code(
                arguments["query"], arguments.get("path", "."), arguments.get("max_results", 50),  # type: ignore[arg-type]
            )
        if name == "retrieve_repair_experience":
            return self.retrieve_repair_experience(
                arguments["query"], arguments.get("limit", 5), arguments.get("outcome", "accepted"),
                arguments.get("file_paths", ()),  # type: ignore[arg-type]
            )
        if name == "apply_patch":
            return self.apply_model_patch(arguments["patch_diff"], remaining_seconds=remaining_seconds)
        if name == "run_tests":
            return self.run_tests(remaining_seconds=remaining_seconds)
        raise RepairToolError(f"模型不能直接调用工具：{name}")
