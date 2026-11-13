"""使用本地 SQLite 和假嵌入函数测试修复知识库，不访问真实模型服务。"""

# tempfile：创建测试数据库目录；unittest：组织测试并提供断言。
import tempfile
import unittest
import sqlite3
# Path：表示临时数据库路径。
from pathlib import Path

# KnowledgeBase、KnowledgeBaseError：被测试的知识库和拒绝条件异常。
from knowledge_base import KnowledgeBase, KnowledgeBaseError
# create_initial_state：构造完整的修复状态。
from repair_state import create_initial_state


class KnowledgeBaseTests(unittest.TestCase):
    """验证案例只有在接受且通过验证后才会写入并能被向量检索。"""

    def make_state(self):
        """创建一个可进入知识库的已接受修复状态。"""
        state = create_initial_state(
            "task-kb", {"type": "python_file", "path": "target.py"}, "数据库查询结果错误",
            remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
        )
        state.update(
            review_decision="accepted",
            diagnosis="查询条件缺少租户过滤",
            patch_diff="diff --git a/target.py b/target.py",
            patch_hash="hash-1",
            validation={
                "patch_hash": "hash-1", "passed": True,
                "summary": "回归测试通过", "report_path": "work/validation.md",
            },
        )
        return state

    @staticmethod
    def fake_embed(text: str) -> list[float]:
        """根据文本是否包含数据库关键词返回固定二维向量，模拟嵌入服务。"""
        return [1.0, 0.0] if "数据库" in text else [0.0, 1.0]

    def test_save_and_search_case(self) -> None:
        """接受案例会保存正文和向量，并按相似度返回。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)

            knowledge_base.save_case(self.make_state())

            self.assertEqual(knowledge_base.count(), 1)
            matches = knowledge_base.search("数据库错误", limit=1)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["case_id"], "task-kb")
            self.assertAlmostEqual(matches[0]["vector_score"], 1.0)
            self.assertGreater(matches[0]["keyword_score"], 0)
            self.assertEqual(matches[0]["retrieval_mode"], "hybrid")
            self.assertIn("查询条件缺少租户过滤", matches[0]["document"])

    def test_resaving_same_task_updates_revision(self) -> None:
        """同一任务再次保存时更新案例并递增 revision，而不是产生重复案例。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            state = self.make_state()
            knowledge_base.save_case(state)
            state["diagnosis"] = "修正后的租户过滤条件"
            knowledge_base.save_case(state)

            matches = knowledge_base.search("数据库错误")
            self.assertEqual(knowledge_base.count(), 1)
            self.assertEqual(matches[0]["revision"], 2)
            self.assertIn("修正后的租户过滤条件", matches[0]["document"])

    def test_unaccepted_or_unverified_case_is_rejected(self) -> None:
        """未接受或未通过验证的状态不能被写入知识库。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            state = self.make_state()
            state["review_decision"] = "pending"
            with self.assertRaisesRegex(KnowledgeBaseError, "接受"):
                knowledge_base.save_case(state)

            state["review_decision"] = "accepted"
            state["validation"]["passed"] = False
            with self.assertRaisesRegex(KnowledgeBaseError, "验证通过"):
                knowledge_base.save_case(state)
            self.assertEqual(knowledge_base.count(), 0)

    def test_rejection_is_saved_as_negative_experience(self) -> None:
        """拒绝理由写入审核表，并作为标记为 rejected 的失败经验参与检索。"""
        embed_calls: list[str] = []

        def tracking_embed(text: str) -> list[float]:
            """记录嵌入调用，确认拒绝记录不会被向量化。"""
            embed_calls.append(text)
            return self.fake_embed(text)

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "knowledge.sqlite"
            knowledge_base = KnowledgeBase(database_path, tracking_embed)
            state = self.make_state()
            state["review_decision"] = "rejected"
            state["review_reason"] = "补丁没有修复原始问题。"

            knowledge_base.record_rejection(state)

            self.assertEqual(knowledge_base.count(), 0)
            self.assertEqual(len(embed_calls), 1)
            self.assertEqual(knowledge_base.failure_count(), 1)
            self.assertEqual(knowledge_base.search("数据库查询补丁被拒绝", limit=1), [])
            matches = knowledge_base.search("数据库查询补丁被拒绝", limit=1, outcome="rejected")
            self.assertEqual(matches[0]["outcome"], "rejected")
            self.assertIn("补丁没有修复原始问题", matches[0]["document"])
            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute(
                    "SELECT decision, reason, document FROM repair_reviews WHERE task_id = ?",
                    (state["task_id"],),
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "rejected")
            self.assertEqual(row[1], "补丁没有修复原始问题。")
            self.assertIn("补丁没有修复原始问题", row[2])

    def test_success_and_rejected_experiences_are_retrieved_separately(self) -> None:
        """成功经验和拒绝经验使用独立结果集，不互相占用召回名额。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            accepted = self.make_state()
            knowledge_base.save_case(accepted)

            rejected = self.make_state()
            rejected["task_id"] = "task-kb-rejected"
            rejected["review_decision"] = "rejected"
            rejected["review_reason"] = "拒绝：没有处理数据库查询边界。"
            knowledge_base.record_rejection(rejected)

            successful = knowledge_base.search("数据库查询", limit=1, outcome="accepted")
            failures = knowledge_base.search("数据库查询", limit=1, outcome="rejected")
            self.assertEqual([item["case_id"] for item in successful], ["task-kb"])
            self.assertEqual([item["outcome"] for item in successful], ["accepted"])
            self.assertEqual([item["case_id"] for item in failures], ["task-kb-rejected"])
            self.assertEqual([item["outcome"] for item in failures], ["rejected"])

    def test_strict_filters_require_matching_repository_path_and_dependency_versions(self) -> None:
        """指定仓库、路径或依赖版本后，只返回完整匹配所有过滤条件的案例。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            cases = (
                ("same-scope", "repo-a", ["src/target.py"], {"numpy": "1.26.0"}),
                ("other-path", "repo-a", ["src/other.py"], {"numpy": "1.26.0"}),
                ("other-repo", "repo-b", ["src/target.py"], {"numpy": "1.26.0"}),
                ("other-version", "repo-a", ["src/target.py"], {"numpy": "2.0.0"}),
                ("missing-scope", None, [], {}),
            )
            for task_id, repository_id, file_paths, dependencies in cases:
                state = self.make_state()
                state["task_id"] = task_id
                state["source"]["retrieval_scope"] = {
                    **({"repository_id": repository_id} if repository_id else {}),
                    "file_paths": file_paths,
                    "dependency_versions": dependencies,
                }
                state["patch_diff"] = (
                    "--- a/src/target.py\n+++ b/src/target.py\n" if "target.py" in str(file_paths) else
                    "--- a/src/other.py\n+++ b/src/other.py\n"
                )
                knowledge_base.save_case(state)

            matches = knowledge_base.search(
                "数据库查询错误",
                filters={
                    "repository_id": "repo-a",
                    "file_paths": ["src/target.py"],
                    "dependency_versions": {"NumPy": "1.26.0"},
                },
            )
            self.assertEqual([item["case_id"] for item in matches], ["same-scope"])

    def test_language_filter_normalizes_aliases_and_excludes_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            for task_id, language in (("python", "python"), ("typescript", "typescript"), ("legacy", None)):
                state = self.make_state()
                state["task_id"] = task_id
                state["source"]["retrieval_scope"] = {
                    "repository_id": "repo-a", "file_paths": ["src/shared.py"],
                    "dependency_versions": {},
                }
                if language is not None:
                    state["source"]["retrieval_scope"]["language"] = language
                knowledge_base.save_case(state)

            python_matches = knowledge_base.search("数据库查询错误", filters={"language": "py"})
            typescript_matches = knowledge_base.search("数据库查询错误", filters={"language": "TypeScript"})

            self.assertEqual([item["case_id"] for item in python_matches], ["python"])
            self.assertEqual([item["case_id"] for item in typescript_matches], ["typescript"])
            with self.assertRaisesRegex(ValueError, "language"):
                knowledge_base.search("数据库查询错误", filters={"language": "auto"})

    def test_invalid_retrieval_outcome_is_rejected(self) -> None:
        """拒绝未知经验类型，避免调用方无意恢复混合召回。"""
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = KnowledgeBase(Path(directory) / "knowledge.sqlite", self.fake_embed)
            with self.assertRaisesRegex(ValueError, "outcome"):
                knowledge_base.search("数据库", outcome="all")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
