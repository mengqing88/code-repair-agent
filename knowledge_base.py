"""保存已接受的修复案例，并使用向量相似度检索历史经验。

知识库使用 SQLite 保存案例正文和 JSON 编码的向量，嵌入模型由 llm_client.embed_text
调用兼容 OpenAI 的 embeddings 接口。当前规模不需要额外部署向量数据库。
"""

# json：序列化案例正文和向量；math：计算余弦相似度并检查数字。
import json
import math
import sqlite3
# dataclass：组织不可变的知识库配置；datetime：记录案例写入时间。
from dataclasses import dataclass
from datetime import datetime, timezone
# Path：表示 SQLite 文件路径；Callable、Sequence：描述嵌入函数的输入和向量输出。
from pathlib import Path
from typing import Callable, Sequence

# embed_text：调用配置中的 EMBEDDING_MODEL 生成向量。
from llm_client import embed_text
# RepairState：保存案例时读取工作流的完整修复状态。
from repair_state import RepairState


EmbeddingFunction = Callable[[str], Sequence[float]]


class KnowledgeBaseError(RuntimeError):
    """知识库读写或向量格式不符合约定时抛出的错误。"""


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
    }
    # sort_keys=True 保证同一案例重复保存时正文顺序稳定，便于调试和复现。
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
        connection = sqlite3.connect(self.path)
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
        connection.commit()
        return connection

    def save_case(self, state: RepairState) -> None:
        """嵌入并保存一个已接受且验证通过的修复案例；重复任务号会更新原案例。"""
        document = build_case_document(state)
        vector = _validated_vector(self.embedder(document), "案例向量")
        timestamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO repair_cases
                    (case_id, document, embedding, dimension, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    document = excluded.document,
                    embedding = excluded.embedding,
                    dimension = excluded.dimension,
                    revision = repair_cases.revision + 1,
                    updated_at = excluded.updated_at
                """,
                (state["task_id"], document, json.dumps(vector), len(vector), timestamp, timestamp),
            )
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise KnowledgeBaseError(f"保存修复案例失败：{error}") from error
        finally:
            connection.close()

    def search(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        """向量化查询并按余弦相似度返回历史案例。

        参数：
            query：待检索的错误报告或诊断文字，不能为空。
            limit：最多返回的案例数，必须是正整数。

        返回：
            按 score 从高到低排列的案例字典，包含 case_id、revision、score 和 document。
            不同嵌入维度的旧案例会被跳过，避免混用不同模型的向量。
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("知识库查询文本不能为空")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("知识库查询数量必须是正整数")
        query_vector = _validated_vector(self.embedder(query), "查询向量")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT case_id, document, embedding, dimension, revision FROM repair_cases"
            ).fetchall()
        except sqlite3.Error as error:
            raise KnowledgeBaseError(f"读取知识库失败：{error}") from error
        finally:
            connection.close()

        results: list[dict[str, object]] = []
        for row in rows:
            if row["dimension"] != len(query_vector):
                continue
            try:
                stored_vector = _validated_vector(json.loads(row["embedding"]), "已保存向量")
                score = _cosine_similarity(query_vector, stored_vector)
            except (json.JSONDecodeError, KnowledgeBaseError):
                continue
            results.append({
                "case_id": row["case_id"],
                "revision": row["revision"],
                "score": score,
                "document": row["document"],
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
