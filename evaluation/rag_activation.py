"""真实配置激活冒烟：临时合成知识库 + 真实 Embedding/Qdrant/重排权重。

显式 --run-online 才访问服务；不修改用户知识库，最后仅删除本次新建的测试 collection。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from uuid import uuid4


def run_reranker(report):
    """不启动 Docker、不连接 Qdrant，仅验证当前本地重排配置及实际算子归属。"""
    from collections import Counter
    import onnxruntime as ort
    from retrieval_reranker import configured_reranker
    reranker = configured_reranker()
    if reranker is None or reranker.backend != "onnx":
        raise RuntimeError("需要已配置的 ONNX Cross-Encoder")
    texts = ["先检查列表是否为空，再读取第一个元素；为空时按接口约定处理。",
             "增加数据库连接池容量", "修改时区设置", "调整日志级别", "为 HTTP 请求增加重试",
             "修复字符编码", "避免文件句柄泄漏", "用参数化 SQL 查询", "避免共享可变默认参数", "减少递归深度"]
    query = "Python 空列表读取第一个元素触发 IndexError，如何修复？"
    factory = ort.SessionOptions
    with tempfile.TemporaryDirectory(prefix="reranker-activation-") as temp:
        def profiled_options():
            options = factory()
            options.enable_profiling = True
            options.profile_file_prefix = str(Path(temp) / "execution")
            return options
        # 仅此显式验收进程打开 profiling，不改变业务运行配置。
        ort.SessionOptions = profiled_options
        try:
            started = perf_counter()
            reranker.warmup()
            report["model_load_and_warmup_seconds"] = perf_counter() - started
        finally:
            ort.SessionOptions = factory
        started = perf_counter()
        scores = reranker.score(query, texts)
        report["ten_candidate_seconds"] = perf_counter() - started
        session = reranker._model[0]
        events = json.loads(Path(session.end_profiling()).read_text(encoding="utf-8"))
        providers = dict(Counter(event.get("args", {}).get("provider") for event in events
                                 if event.get("args", {}).get("provider")))
        assert len(scores) == 10 and max(range(10), key=lambda number: scores[number]) == 0
        if reranker.provider == "DmlExecutionProvider":
            assert providers.get("DmlExecutionProvider", 0) > 0
        report.update(status="passed", data="10 synthetic candidate texts, real local weights",
                      backend=reranker.backend, provider=reranker.provider, configured_candidates=reranker.candidate_count,
                      threads=reranker.threads, providers=session.get_providers(), profile_operator_counts=providers,
                      checks={"ten_finite_scores": True, "expected_candidate_ranked_first": True,
                              "requested_provider_executed": bool(providers.get(reranker.provider))},
                      scores=scores, artifacts=reranker.artifact_identity())


def run(report):
    from execution_manifest import SnapshotKnowledge, freeze
    from knowledge_base import KnowledgeBase, _case_search_text, build_case_document, build_rejection_document
    from llm_client import embed_texts
    from qdrant_store import QdrantVectorIndex
    from repair_state import create_initial_state
    from resilience import execution_scope
    from retrieval_reranker import configured_reranker
    from runtime_config import load_settings, setting
    from urllib.parse import urlsplit

    url = setting("QDRANT_URL", load_settings()) or ""
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("激活验收仅允许显式配置的本机 Qdrant")
    index = QdrantVectorIndex.from_environment()
    reranker = configured_reranker()
    if index is None or reranker is None:
        raise RuntimeError("必须先启用真实 Qdrant 和 Cross-Encoder")
    # 与业务使用相同 endpoint，测试 collection 独占且名称随机；不删现有 collection。
    collection = "activation_smoke_" + uuid4().hex
    original_collection = os.environ.get("QDRANT_COLLECTION")
    os.environ["QDRANT_COLLECTION"] = collection
    index = QdrantVectorIndex.from_environment()
    report.update(collection=collection, data="synthetic, temporary; no private user cases",
                  backend=reranker.backend, provider=reranker.provider, candidates=reranker.candidate_count)
    try:
        # 服务不可用时立即结束，不能先付费调用 Embedding 再发现索引离线。
        index.client.get_collections()
        reports = ["Python 列表为空时读取第一个元素触发 IndexError", "除数为零导致 ZeroDivisionError",
                   "字典缺少键导致 KeyError", "文件不存在导致 FileNotFoundError", "JSON 解码失败",
                   "Unicode 文本解码错误", "超时连接未及时清理", "线程锁未释放导致死锁",
                   "循环边界错误漏掉最后一个元素", "可变默认参数导致列表状态串用"]
        states = []
        for number, issue in enumerate(reports):
            state = create_initial_state(f"smoke-{number}", {"type": "python_file", "path": f"src/m{number}.py",
                "retrieval_scope": {"repository_id": "activation-fixture", "file_paths": [f"src/m{number}.py"]}}, issue)
            patch = f"patch-{number}"
            patch_hash = hashlib.sha256(patch.encode()).hexdigest()
            state.update(review_decision="accepted", diagnosis=issue + "，先检查边界条件再执行操作。",
                         patch_diff=patch, patch_hash=patch_hash, applicable_conditions=[issue],
                         validation={"passed": True, "patch_hash": patch_hash, "summary": "synthetic fixture"})
            states.append(state)
        rejected = deepcopy(states[0])
        rejected.update(task_id="smoke-rejected", review_decision="rejected", review_reason="不能用捕获所有异常并返回常量掩盖错误")
        query = "Python 空列表访问第一个元素 IndexError，应该先检查边界"
        documents = [build_case_document(state) for state in states] + [build_rejection_document(rejected)]
        texts = [_case_search_text(json.loads(doc)) for doc in documents]
        texts.append(query)
        started = perf_counter()
        vectors = embed_texts(texts)
        report.update(embedding_dimension=len(vectors[0]), embedding_batch_seconds=perf_counter() - started)
        cache = dict(zip(texts, vectors))
        # 查询仍经过真实业务检索代码；向量在本次验收内复用，不重复付费请求相同文本。
        embedder = lambda text: cache[text]
        started = perf_counter()
        reranker.warmup()
        report["model_load_and_warmup_seconds"] = perf_counter() - started
        report["providers"] = reranker._model[0].get_providers() if reranker.backend == "onnx" else ["torch"]
        with tempfile.TemporaryDirectory(prefix="rag-activation-") as temp:
            root = Path(temp)
            live = KnowledgeBase(root / "user-a.sqlite3", embedder=embedder, qdrant_index=index, reranker=reranker)
            for state in states:
                live.save_case(state)
            live.record_rejection(rejected)
            task = {"state": states[0], "config": {"allowed_paths": ["src/m0.py"], "test_targets": ["."],
                    "execution_backend": "host", "source_fingerprint": {}}}
            directory = root / "task"
            directory.mkdir()
            manifest, _ = freeze(task, directory, {"error_report": query}, live)
            frozen = SnapshotKnowledge(live, directory, manifest)
            snapshot_hash = hashlib.sha256(frozen.snapshot.path.read_bytes()).hexdigest()
            with execution_scope(120, task_id="activation-smoke", trace_path=root / "trace.jsonl") as ctx:
                started = perf_counter()
                accepted = frozen.search(query, min_similarity=0)
                rejected_hits = frozen.search(query, outcome="rejected", min_similarity=0)
                report["snapshot_queries_seconds"] = perf_counter() - started
                # 改写 live，再建另一个用户；冻结任务必须仍保留旧版本。
                live_connection = live._connect()
                live_connection.execute("DELETE FROM repair_cases WHERE case_id = 'smoke-0'")
                live_connection.commit()
                live_connection.close()
                other = KnowledgeBase(root / "user-b.sqlite3", embedder=embedder, qdrant_index=index, reranker=None)
                other_state = deepcopy(states[0])
                other_state["task_id"] = "other-user"
                other.save_case(other_state)
                repeated = frozen.search(query, min_similarity=0)
                assert not ctx.warnings, ctx.warnings
            events = [json.loads(line) for line in (root / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
            qdrant_events = [e for e in events if e["operation"] == "knowledge.qdrant" and e["event"] == "completed"]
            rerank_events = [e for e in events if e["operation"] == "knowledge.rerank" and e["event"] == "completed"]
            assert len(accepted) == 5 and all("rerank_score" in item for item in accepted)
            assert len(rejected_hits) == 1 and rejected_hits[0]["outcome"] == "rejected"
            assert [i["case_id"] for i in repeated] == [i["case_id"] for i in accepted]
            assert all(i["case_id"] != "other-user" for i in repeated)
            assert hashlib.sha256(frozen.snapshot.path.read_bytes()).hexdigest() == snapshot_hash
            assert len(qdrant_events) == 3 and len(rerank_events) == 3
            assert rerank_events[0]["details"]["candidates"] == 10
            # 服务端确实存入冻结 namespace，而非仅配置客户端后静默降级。
            matches = index.search(namespace=frozen.snapshot.qdrant_namespace, vector=cache[query],
                                   limit=20, outcome="accepted", filters={})
            assert len(matches) == 10 and {m["case_id"] for m in matches} == {s["task_id"] for s in states}
            report.update(status="passed", checks={"frozen_remote_index": True, "ten_candidates_reranked": True,
                "top_five_returned": True, "accepted_rejected_separated": True, "live_changes_excluded": True,
                "other_user_excluded": True, "snapshot_unchanged": True, "no_fallback_warning": True},
                qdrant_completed_events=len(qdrant_events), rerank_completed_events=len(rerank_events),
                returned_ids=[i["case_id"] for i in accepted], artifacts=reranker.artifact_identity())
    finally:
        try:
            if index.client.collection_exists(collection):
                index.client.delete_collection(collection)
            report["test_collection_removed"] = not index.client.collection_exists(collection)
        except Exception as error:
            report["cleanup_status"] = "unconfirmed"
            report["cleanup_error_type"] = type(error).__name__
        if original_collection is None:
            os.environ.pop("QDRANT_COLLECTION", None)
        else:
            os.environ["QDRANT_COLLECTION"] = original_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-online", action="store_true")
    mode.add_argument("--reranker-only", action="store_true", help="只验证本地模型，不连接 Qdrant/Embedding")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告路径已存在，请选择新路径，不能覆盖验收证据")
    report = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
              "limitations": ["合成数据只验证接线、隔离与模型执行，不证明独立相关性或修复成功率提升",
                              "没有并发、长输入和生产规模性能验收；GPU 可包含 CPU 算子",
                              "计时包含单进程冷启动；实际 Web worker 每次新建进程仍需重新加载"]}
    try:
        (run_reranker if args.reranker_only else run)(report)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("status", "checks", "test_collection_removed") if key in report}))


if __name__ == "__main__":
    main()
