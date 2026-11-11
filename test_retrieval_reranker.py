"""验证初召回候选可重排，重排失败时初始排序仍可用。"""

import hashlib

from knowledge_base import KnowledgeBase
from repair_state import create_initial_state


def make_case(case_id: str):
    """创建通过验收门槛的测试案例。"""
    patch = f"patch {case_id}"
    state = create_initial_state(
        case_id, {"type": "python_file", "path": "worker.py"}, f"issue in {case_id}",
        remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
    )
    state.update(
        review_decision="accepted", diagnosis=f"diagnosis {case_id}", patch_diff=patch,
        patch_hash=hashlib.sha256(patch.encode()).hexdigest(),
        applicable_conditions=["same test fixture"],
        validation={"passed": True, "patch_hash": hashlib.sha256(patch.encode()).hexdigest(),
                    "summary": "tests passed"},
    )
    return state


class FakeReranker:
    """用候选文本中编号生成确定性排序。"""

    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    def score(self, query, documents):
        self.calls.append((query, list(documents)))
        if self.fail:
            raise RuntimeError("model unavailable")
        return [float("5" in text) for text in documents]


def test_reranker_scores_only_initial_five_candidates(tmp_path):
    reranker = FakeReranker()
    knowledge = KnowledgeBase(
        tmp_path / "knowledge.sqlite3", embedder=lambda _text: [1.0, 0.0], reranker=reranker,
    )
    for index in range(1, 7):
        knowledge.save_case(make_case(f"case-{index}"))

    results = knowledge.search("unmatched query", limit=5)

    assert len(reranker.calls) == 1
    assert len(reranker.calls[0][1]) == 5
    assert results[0]["case_id"] == "case-5"
    assert results[0]["rerank_score"] == 1.0
    assert all("_rerank_text" not in item for item in results)


def test_reranker_failure_preserves_initial_results(tmp_path):
    reranker = FakeReranker(fail=True)
    knowledge = KnowledgeBase(
        tmp_path / "knowledge.sqlite3", embedder=lambda _text: [1.0, 0.0], reranker=reranker,
    )
    for index in range(1, 4):
        knowledge.save_case(make_case(f"case-{index}"))

    results = knowledge.search("unmatched query", limit=3)

    assert [item["case_id"] for item in results] == ["case-1", "case-2", "case-3"]
