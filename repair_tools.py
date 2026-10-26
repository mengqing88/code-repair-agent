"""为代码修复工作流提供受限工具。

工具只操作指定工作区。读取、搜索和经验检索可以作为模型工具使用；补丁应用、测试和
经验保存由固定工作流节点调用，避免模型直接执行有副作用的动作。
"""

from __future__ import annotations

# json：编码知识库文档和工具参数；math：检查向量中的有限数字。
import json
import math
# os、shutil、subprocess：读取运行环境、查找 rg、执行受限的 git/pytest 子进程。
import os
import shutil
import subprocess
import sys
# dataclass：保存工具箱配置；PurePosixPath：按 Git 的斜杠路径解析补丁文件名。
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
# re：解析 unified diff 的文件头；typing：标注工具输入输出。
import re
from typing import Any, Callable, Mapping, Sequence

from knowledge_base import KnowledgeBaseError, build_case_document
from llm_client import embed_text
from reproduction import fenced
from repair_state import RepairState


class RepairToolError(RuntimeError):
    """工具输入、权限范围或外部程序执行失败时抛出的错误。"""


EmbeddingFunction = Callable[[str], Sequence[float]]
ConnectionFactory = Callable[[str], Any]


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
        except (OSError, UnicodeDecodeError):
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
    completed = subprocess.run(
        [rg_path, "--fixed-strings", "--line-number", "--column", "--no-heading", "--color", "never", "--", query, search_argument],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
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


@dataclass(frozen=True)
class PostgresKnowledgeBase:
    """使用 PostgreSQL pgvector 保存和检索已验收的修复案例。"""

    dsn: str | None = None
    embedder: EmbeddingFunction = embed_text
    connection_factory: ConnectionFactory | None = None

    def _connect(self) -> Any:
        """打开数据库连接；默认从 KNOWLEDGE_DB_URL 或 DATABASE_URL 读取连接串。"""
        dsn = self.dsn or os.getenv("KNOWLEDGE_DB_URL") or os.getenv("DATABASE_URL")
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
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise KnowledgeBaseError(f"初始化 PostgreSQL 知识库失败：{error}") from error
        finally:
            connection.close()

    def search(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        """向量化查询并按 pgvector 余弦相似度返回案例。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("知识库查询文本不能为空")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("知识库查询数量必须是正整数")
        vector = _validated_vector(self.embedder(query), "查询向量")
        self.ensure_schema()
        connection = self._connect()
        literal = _vector_literal(vector)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT case_id, revision, document,
                           1 - (embedding <=> %s::vector) AS score
                    FROM repair_cases
                    WHERE dimension = %s
                    ORDER BY embedding <=> %s::vector
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
            }
            for row in rows
        ]

    def save_case(self, state: RepairState) -> None:
        """保存已接受且独立验证通过的案例，并递增同一任务的版本号。"""
        document = build_case_document(state)
        vector = _validated_vector(self.embedder(document), "案例向量")
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


def retrieve_repair_experience(
    query: str,
    *,
    knowledge_base: Any,
    limit: int = 5,
) -> list[dict[str, object]]:
    """查询知识库并补充案例正文中记录的适用条件。"""
    if knowledge_base is None:
        raise RepairToolError("尚未配置修复经验知识库")
    results = knowledge_base.search(query, limit=limit)
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


def _check_patch_scope(patch_diff: str, workspace_root: Path, allowed_paths: Sequence[str]) -> set[str]:
    """确保补丁涉及的每个文件都在调用方明确允许的范围内。"""
    if not allowed_paths:
        raise RepairToolError("应用补丁前必须提供允许修改的路径范围")
    root = workspace_root.resolve()
    allowed = [_workspace_path(root, item, must_exist=False) for item in allowed_paths]
    touched = _diff_paths(patch_diff)
    for relative in touched:
        target = _workspace_path(root, relative, must_exist=False)
        if not any(target == permitted or permitted in target.parents for permitted in allowed):
            raise RepairToolError(f"补丁超出允许修改范围：{relative}")
    return touched


def apply_patch(
    patch_diff: str,
    *,
    workspace_root: Path,
    allowed_paths: Sequence[str],
    max_output_chars: int = 20_000,
) -> dict[str, object]:
    """检查范围后用 git apply --check 和 git apply 应用 unified diff。"""
    if not isinstance(patch_diff, str) or not patch_diff.strip():
        raise ValueError("补丁内容不能为空")
    touched = _check_patch_scope(patch_diff, workspace_root, allowed_paths)
    root = workspace_root.resolve()
    if not (root / ".git").exists():
        raise RepairToolError("应用补丁需要工作区包含 .git")
    command = ["git", "apply", "--whitespace=error"]
    check = subprocess.run(
        [*command, "--check", "-"], cwd=root, input=patch_diff, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if check.returncode != 0:
        detail, _ = _limited_text(check.stderr or check.stdout, max_output_chars)
        raise RepairToolError(f"补丁检查失败：{detail.strip()}")
    applied = subprocess.run(
        [*command, "-"], cwd=root, input=patch_diff, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    if applied.returncode != 0:
        detail, _ = _limited_text(applied.stderr or applied.stdout, max_output_chars)
        raise RepairToolError(f"补丁应用失败：{detail.strip()}")
    output, truncated = _limited_text(applied.stdout, max_output_chars)
    return {"paths": sorted(touched), "return_code": applied.returncode, "output": output, "truncated": truncated}


def run_tests(
    *,
    workspace_root: Path,
    test_targets: Sequence[str],
    timeout_seconds: float = 300.0,
    max_output_chars: int = 30_000,
) -> dict[str, object]:
    """在工作区中固定调用当前 Python 的 pytest，返回真实退出码和输出。"""
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
    command = [os.fspath(sys.executable), "-m", "pytest", *test_targets]
    test_env = os.environ.copy()
    for name in (
        "API_KEY", "CHAT_API_KEY", "EMBEDDING_API_KEY", "BASE_URL",
        "CHAT_BASE_URL", "EMBEDDING_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    ):
        test_env.pop(name, None)
    try:
        completed = subprocess.run(
            command, cwd=root, env=test_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=float(timeout_seconds), check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        stdout, stdout_truncated = _limited_text(stdout, max_output_chars)
        stderr, stderr_truncated = _limited_text(stderr, max_output_chars)
        return {
            "command": command,
            "return_code": None,
            "passed": False,
            "timed_out": True,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
        }
    stdout, stdout_truncated = _limited_text(completed.stdout, max_output_chars)
    stderr, stderr_truncated = _limited_text(completed.stderr, max_output_chars)
    return {
        "command": command,
        "return_code": completed.returncode,
        "passed": completed.returncode == 0,
        "timed_out": False,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
    }


TOOL_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "type": "function", "name": "read_code",
        "description": "读取工作区内指定的 UTF-8 代码文件。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    },
    {
        "type": "function", "name": "search_code",
        "description": "在工作区中搜索文本并返回文件、行号、列号和匹配行。",
        "parameters": {
            "type": "object", "properties": {
                "query": {"type": "string"}, "path": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1},
            }, "required": ["query"], "additionalProperties": False,
        },
    },
    {
        "type": "function", "name": "retrieve_repair_experience",
        "description": "检索已验收的历史修复案例及其相似度。",
        "parameters": {
            "type": "object", "properties": {
                "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1},
            }, "required": ["query"], "additionalProperties": False,
        },
    },
)


@dataclass
class RepairToolbox:
    """绑定工作区和外部知识库的工具集合。"""

    workspace_root: Path
    knowledge_base: PostgresKnowledgeBase | Any | None = None
    allowed_paths: tuple[str, ...] = ()
    test_targets: tuple[str, ...] = ()
    test_timeout_seconds: float = 300.0
    _definitions: tuple[dict[str, object], ...] = field(default=TOOL_DEFINITIONS, init=False, repr=False)

    def model_tool_definitions(self) -> list[dict[str, object]]:
        """返回可交给模型的只读工具定义。"""
        return [dict(definition) for definition in self._definitions]

    def read_code(self, path: str, max_bytes: int = 1_000_000) -> dict[str, object]:
        """调用工作区文件读取工具。"""
        return read_code(path, workspace_root=self.workspace_root, max_bytes=max_bytes)

    def search_code(self, query: str, path: str = ".", max_results: int = 50) -> dict[str, object]:
        """调用工作区文本搜索工具。"""
        return search_code(query, workspace_root=self.workspace_root, path=path, max_results=max_results)

    def retrieve_repair_experience(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        """调用注入的知识库检索工具。"""
        return retrieve_repair_experience(query, knowledge_base=self.knowledge_base, limit=limit)

    def apply_patch(self, patch_diff: str) -> dict[str, object]:
        """由固定工作流调用补丁应用工具。"""
        return apply_patch(
            patch_diff, workspace_root=self.workspace_root, allowed_paths=self.allowed_paths,
        )

    def run_tests(self) -> dict[str, object]:
        """由固定工作流调用测试工具。"""
        return run_tests(
            workspace_root=self.workspace_root,
            test_targets=self.test_targets,
            timeout_seconds=self.test_timeout_seconds,
        )

    def save_repair_experience(self, state: RepairState) -> None:
        """由固定工作流在人工接受后保存修复经验。"""
        save_repair_experience(state, knowledge_base=self.knowledge_base)

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
                relative = _relative_path(self.workspace_root, source_path)
                file_data = self.read_code(relative)
                evidence.append({
                    "path": file_data["path"],
                    "start_line": 1,
                    "end_line": max(1, int(file_data["line_count"])),
                    "quote": file_data["content"],
                    "reason": "任务指定的 Python 源码",
                })
            except (ValueError, RepairToolError):
                # 外部路径不属于工具工作区时不读取，交给调用方明确配置工作区。
                pass
        matches: list[dict[str, object]] = []
        if self.knowledge_base is not None:
            matches = self.retrieve_repair_experience(state["original_report"])
        return {"code_evidence": evidence, "case_matches": matches}

    def apply_patch_for_state(self, state: RepairState) -> None:
        """把状态中的候选补丁交给范围检查和 Git 应用工具。"""
        patch_diff = state["patch_diff"]
        if not isinstance(patch_diff, str):
            raise RepairToolError("状态中没有可应用的补丁")
        self.apply_patch(patch_diff)

    def validate_patch_for_state(self, state: RepairState) -> dict[str, object]:
        """运行固定测试并把退出码、输出和当前补丁哈希绑定到验证摘要。"""
        result = self.run_tests()
        report_path = self.workspace_root / "work" / "validation.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# 验证报告\n\n"
            f"- 退出码：{result['return_code']}\n"
            f"- 是否通过：{result['passed']}\n"
            f"- 是否超时：{result['timed_out']}\n\n"
            "## 标准输出\n\n"
            f"{fenced(str(result['stdout']), 'text')}\n\n"
            "## 标准错误\n\n"
            f"{fenced(str(result['stderr']), 'text')}\n",
            encoding="utf-8",
        )
        summary = "测试通过" if result["passed"] else "测试未通过"
        if result["timed_out"]:
            summary = "测试超时"
        return {
            "patch_hash": state["patch_hash"] or "",
            "passed": result["passed"],
            "summary": summary,
            "report_path": str(report_path),
            "return_code": result["return_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "timed_out": result["timed_out"],
        }

    def as_repair_services(
        self,
        reproduce: Callable[[RepairState], Mapping[str, object]],
        propose_patch: Callable[[RepairState], Mapping[str, object]] | None = None,
    ):
        """把工具箱适配为当前 LangGraph RepairServices 所需的固定节点回调。"""
        # 局部导入避免 repair_workflow 导入 llm_client 时形成模块循环依赖。
        from repair_workflow import RepairServices, llm_propose_patch

        return RepairServices(
            reproduce=reproduce,
            collect_context=self.collect_context_for_state,
            propose_patch=propose_patch or llm_propose_patch,
            apply_patch=self.apply_patch_for_state,
            validate_patch=self.validate_patch_for_state,
            save_case=self.save_repair_experience,
        )

    def call_model_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        """按模型返回的工具名调用只读工具；拒绝调用固定副作用节点。"""
        if name == "read_code":
            return self.read_code(arguments["path"])  # type: ignore[arg-type]
        if name == "search_code":
            return self.search_code(
                arguments["query"], arguments.get("path", "."), arguments.get("max_results", 50),  # type: ignore[arg-type]
            )
        if name == "retrieve_repair_experience":
            return self.retrieve_repair_experience(
                arguments["query"], arguments.get("limit", 5),  # type: ignore[arg-type]
            )
        raise RepairToolError(f"模型不能直接调用工具：{name}")
