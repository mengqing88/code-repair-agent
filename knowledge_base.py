"""保存已接受的修复案例，并使用向量相似度检索历史经验。

知识库使用 SQLite 保存案例正文和 JSON 编码的向量，嵌入模型由 llm_client.embed_text
调用兼容 OpenAI 的 embeddings 接口。当前规模不需要额外部署向量数据库。
"""

# json：序列化案例正文和向量；math：计算余弦相似度并检查数字。
import json
import hashlib
import math
import os
import re
import sqlite3
from collections import Counter
# dataclass：组织不可变的知识库配置；datetime：记录案例写入时间。
from dataclasses import dataclass
from datetime import datetime, timezone
# Path：表示 SQLite 文件路径；Callable、Sequence：描述嵌入函数的输入和向量输出。
from pathlib import Path
from typing import Callable, Sequence

# embed_text：调用配置中的 EMBEDDING_MODEL 生成向量。
from llm_client import embed_text
from resilience import Fault, current, trace, error_info
from diagnostics import redact
# RepairState：保存案例时读取工作流的完整修复状态。
from repair_state import RepairState


EmbeddingFunction = Callable[[str], Sequence[float]]


class KnowledgeBaseError(RuntimeError):
    """知识库读写或向量格式不符合约定时抛出的错误。"""


def default_knowledge_path() -> Path:
    """命令行与 Web 默认共用同一份修复知识库。"""
    root = Path(os.environ.get("REPAIR_DATA_DIR", Path(__file__).parent / "work" / "web"))
    return root / "knowledge.sqlite3"


def _case_search_text(document: dict[str, object]) -> str:
    """仅嵌入缺陷、根因和修复要点，排除路径、时间及大量验证日志。"""
    reproduction = document.get("reproduction") or {}
    parts = [str(document.get(name) or "") for name in ("original_report", "diagnosis")]
    parts.extend(str(item) for item in document.get("applicable_conditions", []))
    if isinstance(reproduction, dict):
        parts.extend(str(reproduction.get(name) or "") for name in ("revised_report", "steps", "reproduction_code"))
    parts.append(str(document.get("patch_diff") or "")[:12_000])
    return "\n".join(parts)[:20_000]


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
        保存到审核记录表的 UTF-8 文本；拒绝记录不作为已验收案例向量化。
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
        "review_reason": reason.strip(),
        "source": state["source"],
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
    """SQLite 修复知识库；embedder 可替换为离线假模型以进行测试。"""

    # path：知识库 SQLite 文件路径；父目录不存在时由类自动创建。
    path: Path
    # embedder：接收案例文本并返回向量的函数，默认调用真实 embeddings 接口。
    embedder: EmbeddingFunction = embed_text

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
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_business_row(connection, "repair_cases", "case_id", state, document)
            self._receipt(connection, document)
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
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存修复案例失败：{error}") from error
        finally:
            connection.close()

    def record_rejection(self, state: RepairState) -> None:
        """记录用户拒绝补丁的理由；拒绝记录不会进入已验收案例表。"""
        document = build_rejection_document(state)
        reason = str(state["review_reason"]).strip()
        timestamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_business_row(connection, "repair_reviews", "task_id", state, document)
            self._receipt(connection, document)
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
            self._receipt(connection, document, write=True)
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存拒绝审核记录失败：{error}") from error
        finally:
            connection.close()

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

    def search(self, query: str, limit: int = 5, min_similarity: float = 0.35) -> list[dict[str, object]]:
        """联合 BM25 和向量召回；向量服务不可用时使用关键词，不返回无关案例。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("知识库查询文本不能为空")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("知识库查询数量必须是正整数")
        if isinstance(min_similarity, bool) or not isinstance(min_similarity, (int, float)) or not 0 <= min_similarity <= 1:
            raise ValueError("min_similarity 必须在 0 到 1 之间")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT case_id, document, embedding, dimension, revision, search_text FROM repair_cases ORDER BY case_id"
            ).fetchall()
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"读取知识库失败：{error}") from error
        finally:
            connection.close()

        if not rows:
            return []
        candidates = []
        for row in rows:
            try:
                document = json.loads(row["document"])
                if not isinstance(document, dict):
                    raise ValueError("案例正文必须为对象")
                candidates.append((row, row["search_text"] or _case_search_text(document)))
            except (ValueError, TypeError) as error:
                error_info(error, operation="knowledge.document.decode")
                if current() and "部分案例损坏，已跳过并记录诊断" not in current().warnings:
                    current().warnings.append("部分案例损坏，已跳过并记录诊断")
                continue
        if not candidates:
            return []
        lexical = _lexical_scores(query, [text for _, text in candidates])
        try:
            query_vector = _validated_vector(self.embedder(query), "查询向量")
        except (ValueError, RuntimeError) as error:
            if isinstance(error, Fault) and error.category == "budget":
                raise
            query_vector = None
            trace("knowledge.search", "degraded", fault=error, decision="keyword_only")
            if current() and "向量检索不可用，本次已降级为关键词检索" not in current().warnings:
                current().warnings.append("向量检索不可用，本次已降级为关键词检索")
        results: list[dict[str, object]] = []
        for index, (row, _) in enumerate(candidates):
            semantic = None
            try:
                stored_vector = _validated_vector(json.loads(row["embedding"]), "已保存向量")
                if query_vector is not None and len(query_vector) == len(stored_vector):
                    semantic = _cosine_similarity(query_vector, stored_vector)
            except (json.JSONDecodeError, KnowledgeBaseError, TypeError) as error:
                error_info(error, operation="knowledge.embedding.decode")
            keyword_score = lexical[index] / (lexical[index] + 1)
            if not keyword_score and (semantic is None or semantic <= 0 or semantic < min_similarity):
                continue
            score = 0.7 * max(0.0, semantic or 0.0) + 0.3 * keyword_score if semantic is not None else keyword_score
            results.append({
                "case_id": row["case_id"],
                "revision": row["revision"],
                "score": score,
                "document": row["document"],
                "vector_score": semantic,
                "keyword_score": keyword_score,
                "retrieval_mode": "hybrid" if semantic is not None else "keyword",
            })
        results.sort(key=lambda item: float(item["score"]), reverse=True)
        return results[:limit]

    def count(self) -> int:
        """返回知识库中当前保存的案例数量。"""
        connection = self._connect()
        try:
            row = connection.execute("SELECT COUNT(*) AS total FROM repair_cases").fetchone()
            return int(row["total"])
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"统计知识库失败：{error}") from error
        finally:
            connection.close()
