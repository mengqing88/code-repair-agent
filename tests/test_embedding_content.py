"""验证两个知识库适配器只为案例向量嵌入精简后的检索文本。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_repair.knowledge_base import KnowledgeBase, _case_search_text
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import PostgresKnowledgeBase


class CapturingCursor:
    """保存 PostgreSQL 测试中执行过的 SQL 和参数。"""

    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> CapturingCursor:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((statement, parameters))


class CapturingConnection:
    """提供无需真实数据库的 PostgreSQL 连接替身。"""

    def __init__(self) -> None:
        self.cursor_instance = CapturingCursor()

    def cursor(self) -> CapturingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class EmbeddingContentTests(unittest.TestCase):
    """确认向量输入精简而完整案例正文保持不变。"""

    @staticmethod
    def make_state(*, rejected: bool = False) -> dict[str, object]:
        """构造包含有效检索信息及应从嵌入文本剔除的内容。"""
        state = create_initial_state(
            "embedding-case",
            {"type": "python_file", "path": "C:/private/project/src/target.py"},
            "空列表输入时触发 IndexError",
        )
        state.update(
            review_decision="rejected" if rejected else "accepted",
            review_reason="补丁没有处理空列表边界。" if rejected else None,
            diagnosis="访问第一个元素前缺少空列表判断",
            applicable_conditions=["输入列表可能为空", "调用方要求空列表返回空结果"],
            patch_diff="diff --git a/target.py b/target.py\n+NOISE_PATCH_CONTENT",
            patch_hash="patch-hash",
            validation={
                "patch_hash": "patch-hash", "passed": True,
                "summary": "NOISE_VALIDATION_LOG", "report_path": "work/validation.md",
            },
            reproduction={
                "revised_report": "空列表时应返回空结果，实际抛出 IndexError。",
                "steps": "NOISE_REPRODUCTION_STEPS",
                "reproduction_code": "NOISE_REPRODUCTION_CODE",
                "execution": {"stdout": "NOISE_STDOUT", "stderr": "NOISE_STDERR"},
            },
        )
        return state

    def assert_embedding_is_curated(self, text: str, *, rejected: bool) -> None:
        """检查语义字段存在，补丁、日志和文件路径没有进入向量文本。"""
        self.assertIn("缺陷表现：空列表输入时触发 IndexError", text)
        self.assertIn("根因：访问第一个元素前缺少空列表判断", text)
        self.assertIn("修订后的报告：", text)
        if rejected:
            self.assertIn("拒绝原因：补丁没有处理空列表边界。", text)
        else:
            self.assertIn("适用条件：", text)
            self.assertNotIn("拒绝原因：", text)
        for noise in (
            "NOISE_PATCH_CONTENT", "NOISE_REPRODUCTION_STEPS", "NOISE_REPRODUCTION_CODE",
            "NOISE_STDOUT", "NOISE_STDERR", "NOISE_VALIDATION_LOG", "C:/private/project",
        ):
            self.assertNotIn(noise, text)

    def test_sqlite_embeds_curated_text_but_keeps_full_case_document(self) -> None:
        """SQLite 用精简字段生成向量，同时保留原补丁和复现证据。"""
        calls: list[str] = []

        def embed(text: str) -> list[float]:
            calls.append(text)
            return [1.0, 0.0]

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "knowledge.sqlite3"
            knowledge = KnowledgeBase(database, embed)
            knowledge.save_case(self.make_state())

            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT document, search_text FROM repair_cases WHERE case_id = ?",
                    ("embedding-case",),
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(len(calls), 1)
        self.assert_embedding_is_curated(calls[0], rejected=False)
        self.assertEqual(row[1], calls[0])
        self.assertIn("NOISE_PATCH_CONTENT", row[0])
        self.assertIn("NOISE_STDOUT", row[0])

    def test_postgres_success_and_failure_use_curated_embedding_text(self) -> None:
        """PostgreSQL 成功案例和拒绝经验共用同一精简文本构造规则。"""
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                calls: list[str] = []
                connection = CapturingConnection()

                def embed(text: str) -> list[float]:
                    calls.append(text)
                    return [1.0, 0.0]

                knowledge = PostgresKnowledgeBase(
                    dsn="postgresql://unused",
                    embedder=embed,
                    connection_factory=lambda _dsn: connection,
                )
                state = self.make_state(rejected=rejected)

                with patch.object(PostgresKnowledgeBase, "ensure_schema"):
                    if rejected:
                        knowledge.record_rejection(state)
                    else:
                        knowledge.save_case(state)

                self.assertEqual(len(calls), 1)
                self.assert_embedding_is_curated(calls[0], rejected=rejected)
                stored_document = json.loads(connection.cursor_instance.executions[-1][1][1])
                self.assertIn("NOISE_PATCH_CONTENT", stored_document["patch_diff"])
                self.assertIn("NOISE_STDOUT", stored_document["reproduction"]["execution"]["stdout"])

    def test_case_search_text_bounds_untrusted_fields(self) -> None:
        """长报告和适用条件受长度限制，非文本条件会被忽略。"""
        text = _case_search_text({
            "original_report": "x" * 5000,
            "applicable_conditions": ["空输入", None, 42],
            "patch_diff": "NOISE_PATCH_CONTENT",
        })

        self.assertLessEqual(len(text), 10_000)
        self.assertIn("x" * 3000, text)
        self.assertNotIn("NOISE_PATCH_CONTENT", text)
        self.assertNotIn("42", text)


if __name__ == "__main__":
    unittest.main()
