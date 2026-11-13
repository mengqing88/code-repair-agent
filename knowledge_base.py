"""保存修复案例正文，并通过 SQLite 与可选的 Qdrant 索引检索历史经验。"""

# json：序列化案例正文和向量；math：计算余弦相似度并检查数字。
import json
import hashlib
import math
import os
import re
import sqlite3
import subprocess
try:
    import tomllib
except ImportError:  # Python < 3.11 can still use exact pins from requirements files.
    tomllib = None
from collections import Counter
# dataclass：组织不可变的知识库配置；datetime：记录案例写入时间。
from dataclasses import dataclass, field
from datetime import datetime, timezone
# Path：表示 SQLite 文件路径；Callable、Sequence：描述嵌入函数的输入和向量输出。
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence
from urllib.parse import urlsplit

# embed_text：调用配置中的 EMBEDDING_MODEL 生成向量。
from llm_client import embed_text
from resilience import Fault, current, trace, error_info
from diagnostics import redact
# RepairState：保存案例时读取工作流的完整修复状态。
from repair_state import RepairState
from qdrant_store import QdrantIndexError, QdrantVectorIndex, configured_qdrant_index
from retrieval_reranker import configured_reranker
from semantic_dedup import (
    DEFAULT_DUPLICATE_THRESHOLD,
    case_provenance,
    collapse_semantic_duplicates,
    merge_similar_cases,
    scope_fingerprint,
    scope_key,
)


EmbeddingFunction = Callable[[str], Sequence[float]]


class KnowledgeBaseError(RuntimeError):
    """知识库读写或向量格式不符合约定时抛出的错误。"""


def default_knowledge_path() -> Path:
    """命令行与 Web 默认共用同一份修复知识库。"""
    root = Path(os.environ.get("REPAIR_DATA_DIR", Path(__file__).parent / "work" / "web"))
    return root / "knowledge.sqlite3"


def _normalize_dependency_name(name: str) -> str:
    """按 Python 包名比较规则规范依赖名称。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependency_versions(root: Path) -> dict[str, str]:
    """读取锁文件或精确版本声明中的依赖版本；范围版本不作为精确过滤条件。"""
    versions: dict[str, str] = {}
    requirement = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*==\s*([^\s;,]+)")
    for path in sorted(root.glob("requirements*.txt")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                match = requirement.match(line.split("#", 1)[0])
                if match:
                    versions[_normalize_dependency_name(match.group(1))] = match.group(2)
        except OSError:
            continue

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib is not None:
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            declarations = list(project.get("dependencies", []))
            declarations.extend(item for group in project.get("optional-dependencies", {}).values() for item in group)
            for declaration in declarations:
                match = requirement.match(str(declaration))
                if match:
                    versions[_normalize_dependency_name(match.group(1))] = match.group(2)
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    for name in ("uv.lock", "poetry.lock"):
        lockfile = root / name
        if not lockfile.is_file() or tomllib is None:
            continue
        try:
            packages = tomllib.loads(lockfile.read_text(encoding="utf-8")).get("package", [])
            for package in packages:
                if isinstance(package, dict) and package.get("name") and package.get("version"):
                    versions[_normalize_dependency_name(package["name"])] = str(package["version"])
        except (OSError, ValueError, TypeError):
            continue

    pipfile_lock = root / "Pipfile.lock"
    if pipfile_lock.is_file():
        try:
            payload = json.loads(pipfile_lock.read_text(encoding="utf-8"))
            for group in ("default", "develop"):
                for name, value in payload.get(group, {}).items():
                    version = value.get("version") if isinstance(value, dict) else None
                    if isinstance(version, str) and version.startswith("=="):
                        versions[_normalize_dependency_name(name)] = version[2:]
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            pass
    return versions


def _git_root(path: Path) -> Path | None:
    """返回路径所属 Git 仓库根目录；不是仓库或 Git 不可用时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 and result.stdout.strip() else None


def _repository_id(root: Path) -> str:
    """用 Git origin URL 或本地根目录生成不暴露原始路径和凭据的稳定标识。"""
    remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3, check=False,
        )
        if result.returncode == 0:
            remote = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if remote:
        if re.match(r"^(?:[^@]+@)?[^:/]+:.+", remote) and "://" not in remote:
            host, repository = remote.rsplit(":", 1)
            host = host.rsplit("@", 1)[-1]
        else:
            parsed = urlsplit(remote)
            host, repository = parsed.hostname or "", parsed.path
        identity = f"remote:{host.lower()}/{repository.strip('/').removesuffix('.git').lower()}"
    else:
        identity = f"local:{root.resolve().as_posix().casefold()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def retrieval_scope_for_path(path: Path, *, file_path: Path | None = None) -> dict[str, object]:
    """从任务来源提取可用于检索过滤的仓库、相对文件路径和精确依赖版本。"""
    location = path.expanduser().resolve()
    scan_root = location if location.is_dir() else location.parent
    repository_root = _git_root(scan_root)
    scope_root = repository_root or scan_root
    scope: dict[str, object] = {}
    if repository_root is not None:
        scope["repository_id"] = _repository_id(repository_root)
    if file_path is not None:
        target = file_path.expanduser().resolve()
        paths = {target.name.casefold()}
        try:
            relative = target.relative_to(scope_root).as_posix()
            paths.add(relative.casefold())
        except ValueError:
            pass
        scope["file_paths"] = sorted(paths)
    dependencies = _dependency_versions(scope_root)
    if dependencies:
        scope["dependency_versions"] = dependencies
    return scope


def _patch_file_paths(patch_diff: object) -> set[str]:
    """从 unified diff 提取案例涉及的相对文件路径。"""
    if not isinstance(patch_diff, str):
        return set()
    paths: set[str] = set()
    for line in patch_diff.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        value = line[4:].split("\t", 1)[0]
        if value == "/dev/null":
            continue
        if value.startswith(("a/", "b/")):
            value = value[2:]
        if value and not Path(value).is_absolute() and ".." not in Path(value).parts:
            paths.add(value.replace("\\", "/").casefold())
    return paths


def _case_retrieval_scope(document: Mapping[str, object]) -> dict[str, object]:
    """读取案例过滤元数据，并兼容从旧正文中的 source 和补丁路径恢复字段。"""
    raw = document.get("retrieval_scope")
    source = document.get("source")
    if not isinstance(raw, Mapping):
        raw = source.get("retrieval_scope", {}) if isinstance(source, Mapping) else {}
    scope = dict(raw) if isinstance(raw, Mapping) else {}
    if isinstance(source, Mapping):
        for key in ("repository_id", "file_paths", "dependency_versions"):
            if key not in scope and key in source:
                scope[key] = source[key]
    patch_paths = _patch_file_paths(document.get("patch_diff"))
    declared_paths = scope.get("file_paths", [])
    if isinstance(declared_paths, list):
        patch_paths.update(
            item.replace("\\", "/").casefold()
            for item in declared_paths if isinstance(item, str) and item.strip()
        )
    scope["file_paths"] = sorted(patch_paths)
    dependencies = scope.get("dependency_versions")
    if isinstance(dependencies, Mapping):
        scope["dependency_versions"] = {
            _normalize_dependency_name(str(name)): str(version)
            for name, version in dependencies.items()
            if isinstance(name, str) and isinstance(version, str) and version.strip()
        }
    return scope


def _normalize_retrieval_filters(filters: Mapping[str, object] | None) -> dict[str, object]:
    """校验并规范可选过滤条件；缺少案例元数据时由匹配函数严格排除。"""
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise ValueError("检索过滤条件必须是对象")
    normalized: dict[str, object] = {}
    repository_id = filters.get("repository_id")
    if repository_id is not None:
        if not isinstance(repository_id, str) or not repository_id.strip():
            raise ValueError("repository_id 必须是非空字符串")
        normalized["repository_id"] = repository_id.strip()
    file_paths = filters.get("file_paths", [])
    if not isinstance(file_paths, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in file_paths):
        raise ValueError("file_paths 必须是非空字符串组成的列表")
    paths = set()
    for item in file_paths:
        path = item.replace("\\", "/")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in Path(path).parts:
            raise ValueError("file_paths 必须是工作区内的相对路径")
        paths.add(path.casefold())
    if paths:
        normalized["file_paths"] = sorted(paths)
    dependency_versions = filters.get("dependency_versions", {})
    if not isinstance(dependency_versions, Mapping) or any(
        not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip()
        for name, version in dependency_versions.items()
    ):
        raise ValueError("dependency_versions 必须是依赖名称到精确版本的映射")
    if dependency_versions:
        normalized["dependency_versions"] = {
            _normalize_dependency_name(name): version.strip()
            for name, version in dependency_versions.items()
        }
    return normalized


def _matches_retrieval_filters(document: Mapping[str, object], filters: Mapping[str, object]) -> bool:
    """仅保留所有已指定过滤条件都匹配且案例具有对应元数据的记录。"""
    scope = _case_retrieval_scope(document)
    repository_id = filters.get("repository_id")
    if repository_id is not None and scope.get("repository_id") != repository_id:
        return False
    requested_paths = filters.get("file_paths")
    if requested_paths:
        case_paths = set(scope.get("file_paths", []))
        if not case_paths.intersection(requested_paths):
            return False
    requested_dependencies = filters.get("dependency_versions")
    if requested_dependencies:
        case_dependencies = scope.get("dependency_versions")
        if not isinstance(case_dependencies, Mapping) or any(
            case_dependencies.get(name) != version for name, version in requested_dependencies.items()
        ):
            return False
    return True


def _case_search_text(document: Mapping[str, object]) -> str:
    """生成面向语义检索的精简文本，完整案例正文仍单独保存供审阅。"""
    parts: list[str] = []

    def add(label: str, value: object, limit: int) -> None:
        """加入有明确检索价值的文本字段，并限制单字段长度。"""
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}：{value.strip()[:limit]}")

    add("缺陷表现", document.get("original_report"), 3000)
    add("根因", document.get("diagnosis"), 2500)

    conditions = document.get("applicable_conditions")
    if isinstance(conditions, list):
        selected_conditions = [item.strip()[:400] for item in conditions[:12]
                               if isinstance(item, str) and item.strip()]
        add("适用条件", "\n".join(selected_conditions), 3000)

    reproduction = document.get("reproduction")
    if isinstance(reproduction, Mapping):
        add("修订后的报告", reproduction.get("revised_report"), 1500)

    add("拒绝原因", document.get("review_reason"), 2000)
    return "\n".join(parts)[:10_000]


def _tokens(text: str) -> Counter:
    """保留异常名、标识符和中文双字词，支持中英文缺陷报告。"""
    terms = []
    for word in re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*|[\u4e00-\u9fff]+|\d+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            terms.extend(word[index:index + 2] for index in range(max(1, len(word) - 1)))
        else:
            terms.append(word.casefold())
    return Counter(terms)


def _lexical_scores(query: str, documents: list[str]) -> list[float]:
    """用 BM25 保留异常名与触发条件的精确匹配信号。"""
    terms = _tokens(query)
    counts = [_tokens(text) for text in documents]
    average = sum(sum(count.values()) for count in counts) / max(1, len(counts)) or 1
    scores = [0.0] * len(counts)
    for term in terms:
        frequency = sum(term in count for count in counts)
        weight = math.log(1 + (len(counts) - frequency + 0.5) / (frequency + 0.5))
        for index, count in enumerate(counts):
            occurrences = count[term]
            if occurrences:
                scores[index] += weight * occurrences * 2.5 / (
                    occurrences + 1.5 * (0.25 + 0.75 * sum(count.values()) / average)
                )
    return scores


def normalize_historical_case(value: object) -> dict[str, object]:
    """接受已审核的案例对象或工作流任务快照，缺少验收证据时拒绝导入。"""
    if not isinstance(value, dict):
        raise KnowledgeBaseError("历史案例必须是 JSON 对象")
    review = value.get("review")
    state = value.get("state", value)
    if not isinstance(state, dict):
        raise KnowledgeBaseError("历史案例的 state 必须是对象")
    state = dict(state)
    state_decision = state.get("review_decision")
    if isinstance(review, dict):
        review_decision = review.get("decision")
        if review_decision != "accepted" or state_decision not in (None, "accepted"):
            raise KnowledgeBaseError("只有接受的历史修复案例才能导入")
        state_decision = review_decision
    if state_decision != "accepted":
        raise KnowledgeBaseError("只有接受的历史修复案例才能导入")
    state["review_decision"] = "accepted"
    task_id = state.get("task_id", state.get("case_id"))
    if not isinstance(task_id, str) or not task_id.strip():
        raise KnowledgeBaseError("历史案例必须提供 task_id 或 case_id")
    for key in ("original_report", "diagnosis", "patch_diff"):
        if not isinstance(state.get(key), str) or not state[key].strip():
            raise KnowledgeBaseError(f"历史案例必须提供非空 {key}")
    validation = state.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise KnowledgeBaseError("只有独立验证通过的历史修复案例才能导入")
    actual_hash = hashlib.sha256(state["patch_diff"].encode("utf-8")).hexdigest()
    claimed_hash = state.get("patch_hash")
    validation_hash = validation.get("patch_hash")
    if claimed_hash and claimed_hash != actual_hash:
        raise KnowledgeBaseError("历史案例的 patch_hash 与补丁内容不一致")
    if validation_hash and validation_hash != actual_hash:
        raise KnowledgeBaseError("历史案例的验证记录与补丁哈希不一致")
    claimed_hash = claimed_hash or validation_hash or actual_hash
    conditions = state.get("applicable_conditions", [])
    if not isinstance(conditions, list) or any(not isinstance(item, str) or not item.strip() for item in conditions):
        raise KnowledgeBaseError("applicable_conditions 必须是非空字符串组成的列表")
    if not isinstance(state.get("source", {}), dict):
        raise KnowledgeBaseError("source 必须是对象")
    return {
        "task_id": task_id.strip(), "source": state.get("source", {}),
        "original_report": state["original_report"], "diagnosis": state["diagnosis"],
        "patch_diff": state["patch_diff"], "patch_hash": claimed_hash,
        "reproduction": state.get("reproduction"), "validation": validation,
        "review_decision": "accepted", "applicable_conditions": conditions,
    }


def build_case_document(state: RepairState) -> str:
    """把已接受且验证通过的状态整理成可嵌入、可审阅的 JSON 文档。

    参数：
        state：修复工作流状态；必须已接受补丁且 validation.passed 为 True。

    返回：
        保存到知识库并发送给嵌入模型的 UTF-8 文本。
    """
    if state["review_decision"] != "accepted":
        raise KnowledgeBaseError("只有接受的修复案例才能进入知识库")
    validation = state["validation"]
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise KnowledgeBaseError("只有独立验证通过的修复案例才能进入知识库")

    # 只保存诊断和验证所需的字段；原始代码路径等输入作为元数据保留，便于追溯来源。
    document = {
        "task_id": state["task_id"],
        "source": state["source"],
        "retrieval_scope": _case_retrieval_scope({
            "source": state["source"], "patch_diff": state["patch_diff"],
        }),
        "original_report": state["original_report"],
        "reproduction": state["reproduction"],
        "diagnosis": state["diagnosis"],
        "patch_diff": state["patch_diff"],
        "validation": validation,
        "review_decision": "accepted",
        "patch_hash": state["patch_hash"],
        "applicable_conditions": state.get("applicable_conditions", []),
    }
    # sort_keys=True 保证同一案例重复保存时正文顺序稳定，便于调试和复现。
    return json.dumps(document, ensure_ascii=False, sort_keys=True, default=str)


def build_rejection_document(state: RepairState) -> str:
    """把用户拒绝补丁的审核记录整理成可审阅的 JSON 文档。

    参数：
        state：修复工作流状态；必须是 rejected 且包含非空 review_reason。

    返回：
        保存到审核记录表和失败经验表的 UTF-8 文本；拒绝记录不会进入已验收案例表。
    """
    if state["review_decision"] != "rejected":
        raise KnowledgeBaseError("只有拒绝的审核结果才能记录拒绝理由")
    reason = state.get("review_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise KnowledgeBaseError("拒绝补丁必须提供理由")

    # 保留审核所需的上下文，便于之后分析补丁为何被拒绝；不把它写入已验收案例表。
    document = {
        "task_id": state["task_id"],
        "review_decision": "rejected",
        "outcome": "rejected",
        "review_reason": reason.strip(),
        "source": state["source"],
        "retrieval_scope": _case_retrieval_scope({
            "source": state["source"], "patch_diff": state.get("patch_diff"),
        }),
        "original_report": state["original_report"],
        "reproduction": state["reproduction"],
        "diagnosis": state["diagnosis"],
        "patch_diff": state["patch_diff"],
        "patch_hash": state["patch_hash"],
        "validation": state["validation"],
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, default=str)


def _validated_vector(values: Sequence[float], name: str) -> list[float]:
    """把嵌入结果转换成有限浮点列表；空向量和非数字都会停止流程。"""
    if isinstance(values, (str, bytes)):
        raise KnowledgeBaseError(f"{name} 必须是数字序列")
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError):
        raise KnowledgeBaseError(f"{name} 必须只包含数字") from None
    if not vector:
        raise KnowledgeBaseError(f"{name} 不能为空")
    if any(not math.isfinite(value) for value in vector):
        raise KnowledgeBaseError(f"{name} 不能包含无效数字")
    return vector


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """计算两个同维向量的余弦相似度；零向量返回 0，避免除零。"""
    if len(left) != len(right):
        raise KnowledgeBaseError("查询向量和案例向量的维度不一致")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass(frozen=True)
class KnowledgeBase:
    """SQLite 保存完整案例，Qdrant 可选地提供向量候选召回。"""

    # path：知识库 SQLite 文件路径；父目录不存在时由类自动创建。
    path: Path
    # embedder：接收案例文本并返回向量的函数，默认调用真实 embeddings 接口。
    embedder: EmbeddingFunction = embed_text
    # qdrant_index：可选向量索引；None 时沿用 SQLite 本地混合检索。
    qdrant_index: QdrantVectorIndex | None = field(default_factory=configured_qdrant_index)
    # reranker：按需对初召回的候选案例重排；可以使用离线假模型测试。
    reranker: object | None = field(default_factory=configured_reranker)
    # duplicate_similarity_threshold：同范围案例自动合并的余弦相似度阈值。
    duplicate_similarity_threshold: float = DEFAULT_DUPLICATE_THRESHOLD
    # 冻结快照使用固定索引身份，不能跟随每次查询的临时复制路径变化。
    index_namespace: str | None = None

    @property
    def qdrant_namespace(self) -> str:
        """返回由数据库路径哈希生成的 Qdrant 隔离命名空间。"""
        return self.index_namespace or QdrantVectorIndex.namespace_for_path(self.path)

    def _index_qdrant(
        self, case_id: str, outcome: str, revision: int, vector: Sequence[float], document: Mapping[str, object],
    ) -> None:
        """把案例向量同步到可重建的 Qdrant 投影；失败时保留 SQLite 主存储。"""
        if self.qdrant_index is None or not vector:
            return
        try:
            self.qdrant_index.upsert(
                namespace=self.qdrant_namespace,
                case_id=case_id,
                outcome=outcome,
                revision=revision,
                vector=vector,
                retrieval_scope=_case_retrieval_scope(document),
            )
        except QdrantIndexError as error:
            trace("knowledge.qdrant", "degraded", fault=error, decision="sqlite_remains_authoritative")
            if current() and "Qdrant 索引不可用，本次经验仍已保存在本地知识库" not in current().warnings:
                current().warnings.append("Qdrant 索引不可用，本次经验仍已保存在本地知识库")

    def _delete_qdrant_case(self, case_id: str, outcome: str) -> None:
        """从 Qdrant 删除已并入主案例的向量点；SQLite 仍保留原始案例。"""
        if self.qdrant_index is None:
            return
        try:
            self.qdrant_index.delete_case(
                namespace=self.qdrant_namespace, case_id=case_id, outcome=outcome,
            )
        except QdrantIndexError as error:
            trace("knowledge.qdrant", "degraded", fault=error, decision="sqlite_merge_remains_authoritative")

    @staticmethod
    def _table_for_outcome(outcome: str) -> str:
        """返回固定结果类型对应的 SQLite 案例表名。"""
        return "repair_cases" if outcome == "accepted" else "repair_failures"

    def _rebuild_scope_merges(self, connection, outcome: str, scope: tuple[object, ...]):
        """重算单一结果类型和适用范围内的主案例关系。"""
        table = self._table_for_outcome(outcome)
        scope_hash = scope_fingerprint(scope)
        before = {
            row[0] for row in connection.execute(
                "SELECT duplicate_case_id FROM repair_case_merges WHERE outcome = ? AND scope_hash = ?",
                (outcome, scope_hash),
            ).fetchall()
        }
        rows = connection.execute(
            f"SELECT case_id, document, embedding, created_at FROM {table} ORDER BY created_at, case_id"
        ).fetchall()
        records = []
        for row in rows:
            try:
                document = json.loads(row["document"])
                if scope_key(document) != scope:
                    continue
                vector = json.loads(row["embedding"])
            except (json.JSONDecodeError, TypeError):
                continue
            records.append({
                "case_id": row["case_id"], "outcome": outcome, "document": document,
                "vector": vector, "created_at": row["created_at"],
            })
        connection.execute(
            "DELETE FROM repair_case_merges WHERE outcome = ? AND scope_hash = ?",
            (outcome, scope_hash),
        )
        links = merge_similar_cases(records, threshold=self.duplicate_similarity_threshold)
        timestamp = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            """INSERT INTO repair_case_merges
               (outcome, duplicate_case_id, canonical_case_id, scope_hash, similarity, merged_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(
                link["outcome"], link["duplicate_case_id"], link["canonical_case_id"],
                link["scope_hash"], link["similarity"], timestamp,
            ) for link in links],
        )
        return before, {link["duplicate_case_id"] for link in links}

    def _ensure_semantic_merges(self) -> None:
        """首次访问时将旧案例并入同范围主案例，且保留原记录。"""
        if not 0 < self.duplicate_similarity_threshold <= 1:
            raise ValueError("duplicate_similarity_threshold 必须大于 0 且不超过 1")
        version = f"same-scope-v1:{self.duplicate_similarity_threshold:.6f}"
        connection = self._connect()
        aliases_to_delete: list[tuple[str, str]] = []
        aliases_to_restore: list[tuple[str, str]] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            marker = connection.execute(
                "SELECT value FROM knowledge_base_meta WHERE key = 'semantic_merge_version'"
            ).fetchone()
            if marker is not None and marker[0] == version:
                connection.commit()
                return
            old_aliases = {
                (row["outcome"], row["duplicate_case_id"])
                for row in connection.execute(
                    "SELECT outcome, duplicate_case_id FROM repair_case_merges"
                ).fetchall()
            }
            connection.execute("DELETE FROM repair_case_merges")
            for outcome in ("accepted", "rejected"):
                table = self._table_for_outcome(outcome)
                rows = connection.execute(
                    f"SELECT case_id, document, embedding, created_at FROM {table} ORDER BY created_at, case_id"
                ).fetchall()
                records = []
                for row in rows:
                    try:
                        records.append({
                            "case_id": row["case_id"], "outcome": outcome,
                            "document": json.loads(row["document"]),
                            "vector": json.loads(row["embedding"]), "created_at": row["created_at"],
                        })
                    except (json.JSONDecodeError, TypeError):
                        continue
                links = merge_similar_cases(records, threshold=self.duplicate_similarity_threshold)
                timestamp = datetime.now(timezone.utc).isoformat()
                connection.executemany(
                    """INSERT INTO repair_case_merges
                       (outcome, duplicate_case_id, canonical_case_id, scope_hash, similarity, merged_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [(
                        link["outcome"], link["duplicate_case_id"], link["canonical_case_id"],
                        link["scope_hash"], link["similarity"], timestamp,
                    ) for link in links],
                )
            connection.execute(
                """INSERT INTO knowledge_base_meta (key, value) VALUES ('semantic_merge_version', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (version,),
            )
            new_aliases = {
                (row["outcome"], row["duplicate_case_id"])
                for row in connection.execute(
                    "SELECT outcome, duplicate_case_id FROM repair_case_merges"
                ).fetchall()
            }
            aliases_to_delete = sorted(new_aliases - old_aliases)
            aliases_to_restore = sorted(old_aliases - new_aliases)
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"迁移语义合并关系失败：{error}") from error
        finally:
            connection.close()
        for outcome, case_id in aliases_to_delete:
            self._delete_qdrant_case(case_id, outcome)
        for outcome in ("accepted", "rejected"):
            old_ids = {case_id for kind, case_id in aliases_to_restore if kind == outcome}
            new_ids = {case_id for kind, case_id in aliases_to_delete if kind == outcome}
            if old_ids or new_ids:
                self._sync_merge_index(outcome, old_ids, new_ids, "")

    def _sync_merge_index(
        self, outcome: str, old_aliases: set[str], new_aliases: set[str], current_case_id: str,
    ) -> None:
        """删除新并入主案例的 Qdrant 点，并恢复不再重复的旧案例点。"""
        for case_id in new_aliases - old_aliases - {current_case_id}:
            self._delete_qdrant_case(case_id, outcome)
        table = self._table_for_outcome(outcome)
        connection = self._connect()
        try:
            for case_id in old_aliases - new_aliases - {current_case_id}:
                row = connection.execute(
                    f"SELECT document, embedding, revision FROM {table} WHERE case_id = ?", (case_id,),
                ).fetchone()
                if row is None:
                    continue
                try:
                    vector = _validated_vector(json.loads(row["embedding"]), "案例向量")
                    document = json.loads(row["document"])
                except (json.JSONDecodeError, KnowledgeBaseError, TypeError):
                    continue
                self._index_qdrant(case_id, outcome, int(row["revision"]), vector, document)
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        """打开数据库并确保案例表存在；调用方负责关闭返回的连接。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_cases (
                case_id TEXT PRIMARY KEY,
                document TEXT NOT NULL,
                embedding TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(repair_cases)")}
        for name in ("search_text", "content_hash"):
            if name not in columns:
                connection.execute(f"ALTER TABLE repair_cases ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_reviews (
                task_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                document TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_failures (
                case_id TEXT PRIMARY KEY,
                document TEXT NOT NULL,
                embedding TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                search_text TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS repair_case_merges (
                outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected')),
                duplicate_case_id TEXT NOT NULL,
                canonical_case_id TEXT NOT NULL,
                scope_hash TEXT NOT NULL,
                similarity REAL NOT NULL,
                merged_at TEXT NOT NULL,
                PRIMARY KEY (outcome, duplicate_case_id),
                CHECK (duplicate_case_id != canonical_case_id)
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS repair_case_merges_scope ON repair_case_merges(outcome, scope_hash)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_base_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.commit()
        connection.execute("""CREATE TABLE IF NOT EXISTS effect_receipts (
            key TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL,
            params_hash TEXT NOT NULL, document_hash TEXT NOT NULL)""")
        connection.commit()
        return connection

    @staticmethod
    def _receipt(connection, document, *, write=False):
        from operation_store import current_effect
        from resilience import Fault, digest
        effect = current_effect()
        if not effect:
            return False
        expected = (effect["task_id"], effect["name"], effect["params_hash"], digest(document))
        row = connection.execute("SELECT task_id,name,params_hash,document_hash FROM effect_receipts WHERE key=?",
                                 (effect["key"],)).fetchone()
        if row and tuple(row) != expected:
            raise Fault("idempotency_conflict", "业务回执与本次操作参数冲突", category="conflict")
        if write and not row:
            connection.execute("INSERT INTO effect_receipts VALUES (?,?,?,?,?)", (effect["key"], *expected))
        return bool(row)

    @staticmethod
    def _check_business_row(connection, table, key, state, document):
        from operation_store import current_effect
        from resilience import Fault
        row = connection.execute(f"SELECT document FROM {table} WHERE {key}=?", (state["task_id"],)).fetchone()
        if current_effect() and row and row[0] != document:
            raise Fault("business_conflict", "任务业务内容与待保存内容冲突，拒绝自动覆盖", category="conflict")
        return row

    def save_case(self, state: RepairState) -> None:
        """相同任务与正文直接复用；正文变化才嵌入并增加版本。"""
        document = build_case_document(state)
        content_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
        self._ensure_semantic_merges()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_business_row(connection, "repair_cases", "case_id", state, document)
            row = connection.execute("SELECT content_hash FROM repair_cases WHERE case_id=?", (state["task_id"],)).fetchone()
            if row and row["content_hash"] == content_hash:
                self._receipt(connection, document, write=True)
                connection.commit()
                return
            self._receipt(connection, document)
        finally:
            connection.close()
        search_text = _case_search_text(json.loads(document))
        vector = _validated_vector(self.embedder(search_text), "案例向量")
        timestamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        old_aliases: set[str] = set()
        new_aliases: set[str] = set()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_business_row(connection, "repair_cases", "case_id", state, document)
            self._receipt(connection, document)
            previous = connection.execute(
                "SELECT document FROM repair_cases WHERE case_id = ?", (state["task_id"],),
            ).fetchone()
            previous_scope = None
            if previous is not None:
                try:
                    previous_scope = scope_key(json.loads(previous["document"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            connection.execute(
                """
                INSERT INTO repair_cases
                    (case_id, document, embedding, dimension, revision, created_at, updated_at, search_text, content_hash)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    document = excluded.document,
                    embedding = excluded.embedding,
                    dimension = excluded.dimension,
                    revision = repair_cases.revision + 1,
                    updated_at = excluded.updated_at,
                    search_text = excluded.search_text,
                    content_hash = excluded.content_hash
                WHERE repair_cases.content_hash != excluded.content_hash
                """,
                (state["task_id"], document, json.dumps(vector), len(vector), timestamp, timestamp,
                 search_text, content_hash),
            )
            self._receipt(connection, document, write=True)
            row = connection.execute(
                "SELECT revision FROM repair_cases WHERE case_id = ?", (state["task_id"],),
            ).fetchone()
            revision = int(row["revision"])
            current_scope = scope_key(json.loads(document))
            scopes = [scope for scope in (previous_scope, current_scope) if scope is not None]
            if len(scopes) == 2 and scopes[0] == scopes[1]:
                scopes.pop()
            for scope in scopes:
                before, after = self._rebuild_scope_merges(connection, "accepted", scope)
                old_aliases.update(before)
                new_aliases.update(after)
            current_merged = connection.execute(
                "SELECT 1 FROM repair_case_merges WHERE outcome = 'accepted' AND duplicate_case_id = ?",
                (state["task_id"],),
            ).fetchone() is not None
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存修复案例失败：{error}") from error
        finally:
            connection.close()
        self._sync_merge_index("accepted", old_aliases, new_aliases, state["task_id"])
        if current_merged:
            self._delete_qdrant_case(state["task_id"], "accepted")
        else:
            self._index_qdrant(state["task_id"], "accepted", revision, vector, json.loads(document))

    def record_rejection(self, state: RepairState) -> None:
        """记录拒绝理由，并把它保存为可检索但明确标记为失败的负向经验。"""
        document = build_rejection_document(state)
        self._ensure_semantic_merges()
        reason = str(state["review_reason"]).strip()
        content_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
        search_text = _case_search_text(json.loads(document))
        try:
            vector = _validated_vector(self.embedder(search_text), "失败经验向量")
        except Exception as error:  # noqa: BLE001 - 失败经验允许降级为关键词检索。
            vector = []
            trace("knowledge.failure_memory", "degraded", fault=error, decision="keyword_only")
        timestamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        old_aliases: set[str] = set()
        new_aliases: set[str] = set()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_business_row(connection, "repair_reviews", "task_id", state, document)
            self._receipt(connection, document)
            previous = connection.execute(
                "SELECT document FROM repair_failures WHERE case_id = ?", (state["task_id"],),
            ).fetchone()
            previous_scope = None
            if previous is not None:
                try:
                    previous_scope = scope_key(json.loads(previous["document"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            connection.execute(
                """
                INSERT INTO repair_reviews (task_id, decision, reason, document, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    decision = excluded.decision,
                    reason = excluded.reason,
                    document = excluded.document,
                    created_at = excluded.created_at
                """,
                (state["task_id"], "rejected", reason, document, timestamp),
            )
            connection.execute(
                """
                INSERT INTO repair_failures
                    (case_id, document, embedding, dimension, revision, created_at, updated_at, search_text, content_hash)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    document = excluded.document,
                    embedding = excluded.embedding,
                    dimension = excluded.dimension,
                    revision = repair_failures.revision + 1,
                    updated_at = excluded.updated_at,
                    search_text = excluded.search_text,
                    content_hash = excluded.content_hash
                WHERE repair_failures.content_hash != excluded.content_hash
                """,
                (state["task_id"], document, json.dumps(vector), len(vector), timestamp, timestamp,
                 search_text, content_hash),
            )
            self._receipt(connection, document, write=True)
            row = connection.execute(
                "SELECT revision FROM repair_failures WHERE case_id = ?", (state["task_id"],),
            ).fetchone()
            revision = int(row["revision"])
            current_scope = scope_key(json.loads(document))
            scopes = [scope for scope in (previous_scope, current_scope) if scope is not None]
            if len(scopes) == 2 and scopes[0] == scopes[1]:
                scopes.pop()
            for scope in scopes:
                before, after = self._rebuild_scope_merges(connection, "rejected", scope)
                old_aliases.update(before)
                new_aliases.update(after)
            current_merged = connection.execute(
                "SELECT 1 FROM repair_case_merges WHERE outcome = 'rejected' AND duplicate_case_id = ?",
                (state["task_id"],),
            ).fetchone() is not None
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存拒绝审核记录失败：{error}") from error
        finally:
            connection.close()
        self._sync_merge_index("rejected", old_aliases, new_aliases, state["task_id"])
        if current_merged:
            self._delete_qdrant_case(state["task_id"], "rejected")
        elif vector:
            self._index_qdrant(state["task_id"], "rejected", revision, vector, json.loads(document))

    def import_cases(self, records: Sequence[object], source: str = "") -> dict[str, object]:
        """逐条校验并导入历史案例；相同正文重复导入不增加版本或重复嵌入。"""
        if isinstance(records, (str, bytes)):
            raise ValueError("records 必须是案例列表")
        results = []
        for index, record in enumerate(records, 1):
            try:
                state = normalize_historical_case(record)
                document = build_case_document(state)
                digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
                connection = self._connect()
                try:
                    row = connection.execute(
                        "SELECT content_hash FROM repair_cases WHERE case_id = ?", (state["task_id"],),
                    ).fetchone()
                finally:
                    connection.close()
                if row is not None and row["content_hash"] == digest:
                    status = "unchanged"
                else:
                    self.save_case(state)
                    status = "updated" if row is not None else "imported"
                results.append({"index": index, "case_id": state["task_id"], "status": status, "source": source})
            except Exception as error:
                if isinstance(error, Fault) and error.category == "budget":
                    raise
                results.append({"index": index, "status": "failed", "error": redact(str(error)), "source": source,
                                "error_info": error_info(error, operation="knowledge.import.case")})
        return {
            **{name: sum(item["status"] == name for item in results) for name in ("imported", "updated", "unchanged", "failed")},
            "results": results,
        }

    def import_path(self, path: Path) -> dict[str, object]:
        """导入 JSON 对象/数组或 JSONL；目录递归读取这两种扩展名。"""
        path = path.expanduser().resolve()
        if not path.exists():
            raise ValueError(f"历史案例路径不存在：{path}")
        files = sorted(item for item in path.rglob("*") if item.suffix.lower() in (".json", ".jsonl") and item.is_file()) if path.is_dir() else [path]
        report: dict[str, object] = {"imported": 0, "updated": 0, "unchanged": 0, "failed": 0, "results": []}
        for file in files:
            try:
                text = file.read_text(encoding="utf-8-sig")
                if file.suffix.lower() == ".jsonl":
                    for line, content in enumerate(text.splitlines(), 1):
                        if not content.strip():
                            continue
                        try:
                            part = self.import_cases([json.loads(content)], f"{file}:{line}")
                        except json.JSONDecodeError as error:
                            part = {"failed": 1, "results": [{"source": f"{file}:{line}", "status": "failed", "error": redact(str(error)),
                                    "error_info": error_info(error, operation="knowledge.import.jsonl")} ]}
                        for key in ("imported", "updated", "unchanged", "failed"):
                            report[key] += part.get(key, 0)
                        report["results"].extend(part["results"])
                else:
                    value = json.loads(text)
                    part = self.import_cases(value if isinstance(value, list) else [value], str(file))
                    for key in ("imported", "updated", "unchanged", "failed"):
                        report[key] += part[key]
                    report["results"].extend(part["results"])
            except Exception as error:
                if isinstance(error, Fault) and error.category == "budget":
                    raise
                report["failed"] += 1
                report["results"].append({"source": str(file), "status": "failed", "error": redact(str(error)),
                                          "error_info": error_info(error, operation="knowledge.import.file")})
        report["total"] = self.count()
        return report

    def search(
        self,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.35,
        outcome: Literal["accepted", "rejected"] = "accepted",
        filters: Mapping[str, object] | None = None,
        strategy: Literal["hybrid", "keyword", "vector"] = "hybrid",
        rerank: bool = True,
    ) -> list[dict[str, object]]:
        """按结果类型和严格元数据过滤分别执行 BM25/向量召回。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("知识库查询文本不能为空")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("知识库查询数量必须是正整数")
        if isinstance(min_similarity, bool) or not isinstance(min_similarity, (int, float)) or not 0 <= min_similarity <= 1:
            raise ValueError("min_similarity 必须在 0 到 1 之间")
        if outcome not in ("accepted", "rejected"):
            raise ValueError("outcome 必须是 accepted 或 rejected")
        if strategy not in ("hybrid", "keyword", "vector"):
            raise ValueError("strategy 必须是 hybrid、keyword 或 vector")
        if not isinstance(rerank, bool):
            raise ValueError("rerank 必须是布尔值")
        normalized_filters = _normalize_retrieval_filters(filters)
        self._ensure_semantic_merges()
        table = "repair_cases" if outcome == "accepted" else "repair_failures"
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT case_id, document, embedding, dimension, revision, search_text, "
                f"'{outcome}' AS outcome FROM {table} AS cases "
                "WHERE NOT EXISTS (SELECT 1 FROM repair_case_merges AS merges "
                "WHERE merges.outcome = ? AND merges.duplicate_case_id = cases.case_id) "
                "ORDER BY case_id",
                (outcome,),
            ).fetchall()
            merged_rows = connection.execute(
                f"""SELECT merges.canonical_case_id, merges.duplicate_case_id,
                           merges.similarity, duplicates.document
                    FROM repair_case_merges AS merges
                    JOIN {table} AS duplicates ON duplicates.case_id = merges.duplicate_case_id
                    WHERE merges.outcome = ? ORDER BY merges.canonical_case_id, merges.duplicate_case_id""",
                (outcome,),
            ).fetchall()
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"读取知识库失败：{error}") from error
        finally:
            connection.close()

        duplicate_provenance: dict[str, list[dict[str, object]]] = {}
        for row in merged_rows:
            duplicate_provenance.setdefault(row["canonical_case_id"], []).append(
                case_provenance(row["duplicate_case_id"], row["document"], float(row["similarity"]))
            )
        if not rows:
            return []
        candidates = []
        documents_by_id: dict[str, dict[str, object]] = {}
        for row in rows:
            try:
                document = json.loads(row["document"])
                if not isinstance(document, dict):
                    raise ValueError("案例正文必须为对象")
                documents_by_id[row["case_id"]] = document
                if not _matches_retrieval_filters(document, normalized_filters):
                    continue
                candidates.append((row, row["search_text"] or _case_search_text(document)))
            except (ValueError, TypeError) as error:
                error_info(error, operation="knowledge.document.decode")
                if current() and "部分案例损坏，已跳过并记录诊断" not in current().warnings:
                    current().warnings.append("部分案例损坏，已跳过并记录诊断")
                continue
        if not candidates:
            return []
        lexical = _lexical_scores(query, [text for _, text in candidates])
        query_vector = None
        if strategy != "keyword":
            try:
                query_vector = _validated_vector(self.embedder(query), "查询向量")
            except (ValueError, RuntimeError) as error:
                if isinstance(error, Fault) and error.category == "budget":
                    raise
                trace("knowledge.search", "degraded", fault=error, decision="keyword_only")
                if current() and "向量检索不可用，本次已降级为关键词检索" not in current().warnings:
                    current().warnings.append("向量检索不可用，本次已降级为关键词检索")

        qdrant_scores: dict[str, float] = {}
        candidate_ids: set[str] | None = None
        if self.qdrant_index is not None and query_vector is not None:
            try:
                if self.qdrant_index.needs_sync(namespace=self.qdrant_namespace, outcome=outcome):
                    index_entries = []
                    for row in rows:
                        document = documents_by_id.get(row["case_id"])
                        if document is None:
                            try:
                                document = json.loads(row["document"])
                            except (json.JSONDecodeError, TypeError):
                                continue
                        try:
                            vector = _validated_vector(json.loads(row["embedding"]), "已保存向量")
                        except (json.JSONDecodeError, KnowledgeBaseError, TypeError):
                            continue
                        index_entries.append({
                            "case_id": row["case_id"], "revision": row["revision"], "vector": vector,
                            "retrieval_scope": _case_retrieval_scope(document),
                        })
                    self.qdrant_index.sync_once(
                        namespace=self.qdrant_namespace, outcome=outcome, entries=index_entries,
                    )
                matches = self.qdrant_index.search(
                    namespace=self.qdrant_namespace,
                    vector=query_vector,
                    limit=max(10, limit * 3),
                    outcome=outcome,
                    filters=normalized_filters,
                )
                active_revisions = {row["case_id"]: row["revision"] for row, _ in candidates}
                qdrant_scores = {
                    item["case_id"]: item["score"] for item in matches
                    if active_revisions.get(item["case_id"]) == item["revision"]
                }
                trace("knowledge.qdrant", "completed", details={"matches": len(qdrant_scores)})
                if qdrant_scores:
                    candidate_ids = set(qdrant_scores)
                    if strategy == "hybrid":
                        lexical_indices = sorted(range(len(candidates)), key=lambda index: lexical[index], reverse=True)
                        candidate_ids.update(
                            candidates[index][0]["case_id"] for index in lexical_indices[:max(10, limit * 3)]
                        )
            except QdrantIndexError as error:
                trace("knowledge.qdrant", "degraded", fault=error, decision="sqlite_vector_scan")
                if current() and "Qdrant 检索不可用，本次已降级为 SQLite 本地检索" not in current().warnings:
                    current().warnings.append("Qdrant 检索不可用，本次已降级为 SQLite 本地检索")

        results: list[dict[str, object]] = []
        stored_vectors: dict[str, Sequence[float]] = {}
        for index, (row, _) in enumerate(candidates):
            if candidate_ids is not None and row["case_id"] not in candidate_ids:
                continue
            semantic = None
            try:
                stored_vector = _validated_vector(json.loads(row["embedding"]), "已保存向量")
                stored_vectors[row["case_id"]] = stored_vector
                if row["case_id"] in qdrant_scores:
                    semantic = qdrant_scores[row["case_id"]]
                elif query_vector is not None and len(query_vector) == len(stored_vector):
                    semantic = _cosine_similarity(query_vector, stored_vector)
            except (json.JSONDecodeError, KnowledgeBaseError, TypeError) as error:
                error_info(error, operation="knowledge.embedding.decode")
            keyword_score = lexical[index] / (lexical[index] + 1)
            if strategy == "keyword":
                if not keyword_score:
                    continue
                score = keyword_score
            elif strategy == "vector":
                if semantic is None or semantic < min_similarity:
                    continue
                score = max(0.0, semantic)
            else:
                if not keyword_score and (semantic is None or semantic <= 0 or semantic < min_similarity):
                    continue
                score = 0.7 * max(0.0, semantic or 0.0) + 0.3 * keyword_score if semantic is not None else keyword_score
            result = {
                "case_id": row["case_id"],
                "revision": row["revision"],
                "score": score,
                "document": row["document"],
                "vector_score": semantic,
                "keyword_score": keyword_score,
                "retrieval_mode": (
                    "keyword" if strategy == "keyword" or semantic is None
                    else "vector" if strategy == "vector" else "hybrid"
                ),
                "outcome": row["outcome"],
                "_rerank_text": candidates[index][1],
            }
            if row["case_id"] in duplicate_provenance:
                result["duplicate_cases"] = duplicate_provenance[row["case_id"]]
            results.append(result)
        results.sort(key=lambda item: float(item["score"]), reverse=True)
        results = collapse_semantic_duplicates(results, stored_vectors)
        if rerank and self.reranker is not None and results:
            rerank_count = getattr(self.reranker, "candidate_count", 0) or max(5, min(10, max(limit, 5)))
            rerank_candidates = results[:rerank_count]
            try:
                scores = self.reranker.score(query, [str(item["_rerank_text"]) for item in rerank_candidates])
                if len(scores) != len(rerank_candidates) or any(not math.isfinite(float(score)) for score in scores):
                    raise ValueError("重排器返回了无效分数")
                for item, score in zip(rerank_candidates, scores):
                    item["rerank_score"] = float(score)
                rerank_candidates.sort(
                    key=lambda item: (float(item["rerank_score"]), float(item["score"])), reverse=True,
                )
                results = rerank_candidates + results[rerank_count:]
                trace("knowledge.rerank", "completed", details={"candidates": len(scores),
                      "backend": getattr(self.reranker, "backend", "custom"),
                      "provider": getattr(self.reranker, "provider", None)})
            except Exception as error:
                if isinstance(error, Fault) and error.category == "budget":
                    raise
                trace("knowledge.rerank", "degraded", fault=error, decision="keep_initial_ranking")
                if current() and "Cross-Encoder 不可用，本次保留初始检索排序" not in current().warnings:
                    current().warnings.append("Cross-Encoder 不可用，本次保留初始检索排序")
        for item in results:
            item.pop("_rerank_text", None)
        return results[:limit]

    def count(self) -> int:
        """返回合并后可独立检索的成功案例数量。"""
        self._ensure_semantic_merges()
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT COUNT(*) AS total FROM repair_cases AS cases
                   WHERE NOT EXISTS (SELECT 1 FROM repair_case_merges AS merges
                                     WHERE merges.outcome = 'accepted'
                                       AND merges.duplicate_case_id = cases.case_id)"""
            ).fetchone()
            return int(row["total"])
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"统计知识库失败：{error}") from error
        finally:
            connection.close()

    def failure_count(self) -> int:
        """返回合并后可独立检索的负向经验数量。"""
        self._ensure_semantic_merges()
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT COUNT(*) AS total FROM repair_failures AS cases
                   WHERE NOT EXISTS (SELECT 1 FROM repair_case_merges AS merges
                                     WHERE merges.outcome = 'rejected'
                                       AND merges.duplicate_case_id = cases.case_id)"""
            ).fetchone()
            return int(row["total"])
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"统计失败经验失败：{error}") from error
        finally:
            connection.close()
