"""验证初召回候选可重排，重排失败时初始排序仍可用。"""

import hashlib
import math
from types import SimpleNamespace

import numpy as np
import pytest

from knowledge_base import KnowledgeBase
from repair_state import create_initial_state
from retrieval_reranker import CrossEncoderReranker, RerankerUnavailable, configured_reranker
from runtime_config import configuration_scope


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


def test_configured_ten_candidates_can_promote_sixth(tmp_path):
    reranker = FakeReranker()
    reranker.candidate_count = 10
    reranker.score = lambda query, docs: [float("case-6" in text) for text in docs]
    kb = KnowledgeBase(tmp_path / "kb.sqlite3", embedder=lambda _: [1., 0.], reranker=reranker)
    for number in range(1, 7):
        kb.save_case(make_case(f"case-{number}"))
    assert kb.search("unmatched query", limit=5)[0]["case_id"] == "case-6"


def test_frozen_settings_select_separate_shared_models(monkeypatch):
    values = {"CROSS_ENCODER_MODEL": "frozen-model", "CROSS_ENCODER_BACKEND": "onnx",
              "CROSS_ENCODER_ONNX_PATH": "frozen.onnx", "CROSS_ENCODER_PROVIDER": "DmlExecutionProvider",
              "CROSS_ENCODER_THREADS": "8", "CROSS_ENCODER_CANDIDATES": "10"}
    manifest = {"settings": {"values": values, "credentials": {}}}
    monkeypatch.setenv("CROSS_ENCODER_MODEL", "different-live-model")
    with configuration_scope(manifest):
        model = configured_reranker()
        assert model is configured_reranker()
        assert (model.model_name, model.backend, model.candidate_count) == ("frozen-model", "onnx", 10)
    values["CROSS_ENCODER_CANDIDATES"] = "5"
    with configuration_scope(manifest):
        assert configured_reranker() is not model


def test_replacing_local_weights_cannot_reuse_loaded_model(tmp_path, monkeypatch):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"first-model")
    monkeypatch.setenv("CROSS_ENCODER_MODEL", str(tmp_path))
    monkeypatch.setenv("CROSS_ENCODER_BACKEND", "onnx")
    monkeypatch.setenv("CROSS_ENCODER_ONNX_PATH", str(path))
    first = configured_reranker()
    path.write_bytes(b"replacement-model")
    second = configured_reranker()
    assert first is not second


def test_onnx_batches_preserve_order_and_truncate_at_model_limit():
    calls = []
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

        def run(self, outputs, feeds):
            assert feeds["input_ids"].dtype == np.int64
            return [feeds["input_ids"][:, :1].astype(float)]

    def tokenizer(queries, docs, **kwargs):
        assert kwargs == dict(padding=True, truncation=True, max_length=512, return_tensors="np")
        calls.append(len(docs))
        return {"input_ids": np.array([[int(doc)] for doc in docs]),
                "attention_mask": np.ones((len(docs), 1))}
    model = CrossEncoderReranker("fake", backend="onnx")
    model._model = Session(), tokenizer
    assert model.score("query", [str(i) for i in range(18)]) == list(map(float, range(18)))
    assert calls == [16, 2]


@pytest.mark.parametrize("output", [np.array([[1., 2.]]), np.array([[math.nan]])])
def test_onnx_rejects_multiclass_or_nonfinite_scores(output):
    model = CrossEncoderReranker("fake", backend="onnx")
    model._model = (SimpleNamespace(get_inputs=lambda: [], run=lambda *_: [output]), lambda *a, **k: {})
    with pytest.raises(RerankerUnavailable):
        model.score("query", ["document"])


def test_onnx_missing_provider_cannot_silently_claim_gpu(monkeypatch):
    onnxruntime = pytest.importorskip("onnxruntime")
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"])
    with pytest.raises(RerankerUnavailable):
        CrossEncoderReranker("fake", backend="onnx", provider="DmlExecutionProvider").score("q", ["d"])
