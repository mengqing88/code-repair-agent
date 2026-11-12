"""Run pinned real multilingual weights on authored diagnostics and public defect texts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import statistics
import tempfile
import time

import numpy as np

from evaluation.run_benchmark import aggregate_ranking_metrics, ranking_metrics
from knowledge_base import KnowledgeBase, _case_search_text, build_case_document
from retrieval_reranker import CrossEncoderReranker
from semantic_dedup import merge_similar_cases

ROOT = Path(__file__).resolve().parent


def model_manifest(path, identifier, revision):
    for name in ["model.safetensors", "config.json"]:
        metadata = path / ".cache/huggingface/download" / (name + ".metadata")
        if not metadata.is_file() or metadata.read_text(encoding="utf-8").splitlines()[0] != revision:
            raise ValueError("Pinned model download metadata is missing or has a different revision")
    return {"repository": identifier, "revision": revision,
            "files": [{"name": file.relative_to(path).as_posix(),
                       "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                      for file in sorted(path.rglob("*")) if file.is_file() and ".cache" not in file.parts]}


def evaluate(embedding_path, reranker_path, public_path):
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    start = time.perf_counter()
    embedding_model = SentenceTransformer(str(embedding_path.resolve()), device="cpu", local_files_only=True)
    reranker = CrossEncoderReranker(str(reranker_path.resolve()))
    cold = time.perf_counter()
    reranker.score("database query", ["The database query misses its tenant filter.", "A recipe for bread."])
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "status": "running", "device": "cpu", "torch_threads": 2,
              "packages": {name: version(name) for name in ["torch", "sentence-transformers", "transformers"]},
              "model_load_seconds": time.perf_counter() - start,
              "cross_encoder_first_pair_seconds": time.perf_counter() - cold,
              "models": [model_manifest(embedding_path, "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                                        "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"),
                         model_manifest(reranker_path, "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                                        "1427fd652930e4ba29e8149678df786c240d8825")]}
    benchmark = json.loads((ROOT / "benchmark.json").read_text(encoding="utf-8"))
    records = benchmark["cases"]
    histories = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "history_cases").glob("*.json"))]
    labels = json.loads((ROOT / "relevance.json").read_text(encoding="utf-8"))["labels"]
    report["synthetic_inputs_sha256"] = hashlib.sha256(json.dumps(
        {"tasks": records, "histories": histories, "labels": labels},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    states = []
    for document in histories:
        states.append({"task_id": document["experience_id"], "review_decision": "accepted",
                       "source": {"project": "authored-synthetic-diagnostic"},
                       "original_report": document["original_report"], "diagnosis": document["diagnosis"],
                       "patch_diff": document["patch_diff"], "patch_hash": "diagnostic-only",
                       "applicable_conditions": document["applicable_conditions"],
                       "reproduction": {}, "validation": {"passed": True}})
    inputs = [_case_search_text(json.loads(build_case_document(state))) for state in states]
    inputs += [record["query"] for record in records]
    vectors = embedding_model.encode(inputs, normalize_embeddings=True, show_progress_bar=False)
    cache = dict(zip(inputs, vectors.tolist()))
    strategies = ["keyword", "vector", "hybrid", "hybrid+cross_encoder"]
    with tempfile.TemporaryDirectory(prefix="repair-real-reranker-") as temp:
        kb = KnowledgeBase(Path(temp) / "knowledge.sqlite3", embedder=cache.__getitem__,
                           qdrant_index=None, reranker=reranker)
        for state in states:
            kb.save_case(state)
        results = {}
        for strategy in strategies:
            details, durations = [], []
            for record in records:
                started = time.perf_counter()
                matches = kb.search(record["query"], limit=10, min_similarity=0,
                                    strategy="hybrid" if strategy == "hybrid+cross_encoder" else strategy,
                                    rerank=strategy == "hybrid+cross_encoder")
                durations.append(time.perf_counter() - started)
                if strategy == "hybrid+cross_encoder" and (not matches or any("rerank_score" not in row for row in matches)):
                    raise RuntimeError("Cross-Encoder silently degraded; do not count as measured")
                ids = [row["case_id"] for row in matches]
                details.append({"case_id": record["case_id"], "ranked_ids": ids,
                                "metrics": ranking_metrics(ids, labels[record["case_id"]])})
            results[strategy] = {"metrics": aggregate_ranking_metrics([row["metrics"] for row in details]),
                                 "p50_seconds": statistics.median(durations),
                                 "p95_seconds": float(np.percentile(durations, 95)), "details": details}
        report["synthetic_retrieval"] = {"queries": len(records), "documents": len(histories),
                                         "strategies": results, "labels": "author assigned, not independent",
                                         "role": "development diagnostic, never frozen test score"}
    public = json.loads((public_path / "documents.json").read_text(encoding="utf-8"))
    report["public_inputs_sha256"] = hashlib.sha256((public_path / "documents.json").read_bytes()).hexdigest()
    texts = [_case_search_text(doc) for doc in public]
    vectors = embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    records = []
    for i, doc in enumerate(public):
        for suffix in ["original", "exact-copy"]:
            records.append({"case_id": doc["case_id"] + ":" + suffix, "outcome": "accepted",
                            "document": doc, "vector": vectors[i].tolist(), "created_at": f"{i}:{suffix}"})
    links = merge_similar_cases(records)
    group = {record["case_id"]: record["document"]["case_id"] for record in records}
    false_merges = [link for link in links if group[link["duplicate_case_id"]] != group[link["canonical_case_id"]]]
    scores = vectors @ vectors.T
    report["public_merge_diagnostic"] = {
        "documents": len(public), "exact_copy_positive_pairs": len(public),
        "distinct_issue_negative_pairs": len(public) * (len(public) - 1) // 2,
        "threshold": 0.97, "links": links, "observed_false_merge_links": len(false_merges),
        "cosine_matrix": scores.tolist(), "positive_basis": "byte-identical text",
        "negative_basis": "different upstream fixes; engineering proxy, not independent production semantic labels",
        "production_false_merge_rate": None,
        "production_status": "Unmeasured: independent representative private-corpus labels are absent",
    }
    # Meaningful technical acceptance: scores are real finite predictions, no fallback.
    # Quality can improve or degrade; record both instead of changing labels to force a pass.
    report["status"] = "passed"
    report["quality_acceptance"] = "diagnostic_only_pending_independent_corpus"
    report["limitations"] = ["Authored 30-query synthetic labels and three public issues do not establish production quality",
                             "Single CPU run; precomputed embedding time excluded from per-query timings",
                             "Model/corpus defaults remain unchanged; do not enable solely because loading succeeded"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--reranker-model", type=Path, required=True)
    parser.add_argument("--public-defects", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path")
    try:
        report = evaluate(args.embedding_model, args.reranker_model, args.public_defects)
    except Exception as error:
        report = {"status": "failed", "reason": f"{type(error).__name__}: {error}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"models", "synthetic_retrieval"}}, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
