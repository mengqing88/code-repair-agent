"""使用本地 SQLite 和假嵌入函数测试修复知识库，不访问真实模型服务。"""

# tempfile：创建测试数据库目录；unittest：组织测试并提供断言。
import tempfile
import unittest
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
            self.assertAlmostEqual(matches[0]["score"], 1.0)
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


if __name__ == "__main__":
    unittest.main()
