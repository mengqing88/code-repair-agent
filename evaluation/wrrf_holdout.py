"""Freeze a query holdout before measurement; compare fixed strategies without a sweep."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile

from code_repair import knowledge_base as kb_module
from code_repair.knowledge_base import KnowledgeBase, _case_search_text, _weighted_reciprocal_rank_scores, build_case_document
from code_repair.retrieval_reranker import CrossEncoderReranker
from code_repair.runtime_config import load_settings, setting
from code_repair.semantic_dedup import collapse_semantic_duplicates
from evaluation.run_benchmark import ranking_metrics, aggregate_ranking_metrics, legacy_hybrid_score


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "retrieval_holdout_v1.json"
PROTOCOL = ROOT / "wrrf-holdout-protocol-20261118.json"
RESULT = ROOT / "results-wrrf-holdout-20261118.json"
STRATEGIES = {"wrrf_093": (0.93, 0.07), "wrrf_070": (0.7, 0.3),
              "vector": (1.0, 0.0), "legacy_score": None}
RULES = {"strategies": STRATEGIES, "rank_constant": 60, "min_similarity": 0.35,
         "return_limit": 5, "per_route_candidate_limit": 15, "rerank_candidates": 10,
         "primary_metrics": ["recall@5", "mrr@10"], "bootstrap_samples": 10000,
         "bootstrap_seed": 20261118, "baseline": "wrrf_070",
         "observed_non_regression": "recall@5 delta >= 0 and mrr@10 delta >= -0.01, before and after reranking",
         "superiority": "positive lower bound of paired 95% recall@5 delta CI; no observed mrr@10 regression beyond 0.01",
         "retune": False, "qdrant": False,
         "scope": "new synthetic queries against unchanged 30-document development corpus; not a new-domain or production test"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def corpus():
    return [load(path) for path in sorted((ROOT / "history_cases").glob("*.json"))]


def validate_dataset(data, histories, development):
    rows = data["queries"]
    ids = [row["case_id"] for row in rows]
    groups = [row["family"] for row in rows]
    queries = [row["query"].strip() for row in rows]
    if not rows or len(ids) != len(set(ids)) or len(groups) != len(set(groups)) or len(queries) != len(set(queries)):
        raise ValueError("Holdout IDs, families and query texts must be unique and nonempty")
    known = {item["experience_id"] for item in histories}
    old_queries = [row["query"] for row in development["cases"]]
    old_reports = [row["original_report"] for row in histories]
    audit = []
    for row in rows:
        labels = row["relevant"]
        if not labels or len(labels) != len(set(labels)) or not set(labels) <= known or not row["rationale"].strip():
            raise ValueError("Every query needs valid, unique relevance labels and a rationale")
        query = row["query"].strip()
        if not query or query in old_queries + old_reports:
            raise ValueError("Holdout query reuses a development query or document verbatim")
        best = max(((SequenceMatcher(None, query.lower(), old.lower()).ratio(), index)
                    for index, old in enumerate(old_queries)), default=(0.0, 0))
        audit.append({"case_id": row["case_id"], "nearest_development_query_id": development["cases"][best[1]]["case_id"],
                      "character_sequence_similarity": best[0], "review_suggested": best[0] >= 0.8})
    return {"exact_query_overlap": 0, "text_similarity_audit": audit,
            "semantic_family_overlap": "intentional: fixed corpus, query-level holdout; not family-disjoint",
            "independent_human_review": False}


def model_identity():
    settings = load_settings()
    reranker = CrossEncoderReranker.from_environment()
    if reranker is None or (reranker.candidate_count or 10) != 10:
        raise ValueError("This protocol requires the configured Cross-Encoder with 10 candidates")
    embedding_model = setting("EMBEDDING_MODEL", settings)
    if not embedding_model:
        raise ValueError("Configured embedding model required")
    # Only nonsecret identifiers and file basenames/hashes enter the public report.
    return {"embedding_model": embedding_model,
            "embedding_endpoint_sha256": digest(setting("EMBEDDING_BASE_URL", settings) or setting("BASE_URL", settings)),
            "reranker": {"model": Path(reranker.model_name).name, "backend": reranker.backend,
                         "provider": reranker.provider, "threads": reranker.threads,
                         "candidate_count": reranker.candidate_count or 10,
                         "artifacts": {Path(path).name: value for path, value in reranker.artifact_identity().items()}}}


def identity():
    data, histories, development = load(DATA), corpus(), load(ROOT / "benchmark.json")
    audit = validate_dataset(data, histories, development)
    source_files = [Path(__file__), ROOT / "run_benchmark.py", Path(kb_module.__file__),
                    Path(kb_module.__file__).with_name("semantic_dedup.py"),
                    Path(kb_module.__file__).with_name("retrieval_reranker.py"),
                    Path(kb_module.__file__).with_name("llm_client.py")]
    return {"dataset_sha256": digest(data), "corpus_sha256": digest(histories),
            "development_sha256": digest(development), "rules": json.loads(json.dumps(RULES)),
            "models": model_identity(), "query_count": len(data["queries"]), "corpus_size": len(histories),
            "leakage_audit": audit,
            "source_sha256": {path.name: hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
                              for path in source_files}}


def prepare(path=PROTOCOL):
    frozen = {"prepared_at": datetime.now(timezone.utc).isoformat(), "identity": identity(),
              "status": "frozen_before_retrieval_measurement", "no_weight_search": True}
    write_once(path, frozen)
    return frozen


def rank_candidates(matches, strategy):
    """Use the same eligible scores and per-route limits for every fixed strategy."""
    vector_ids = [item["case_id"] for item in sorted(
        (item for item in matches if item.get("vector_score") is not None
         and float(item["vector_score"]) >= RULES["min_similarity"] and float(item["vector_score"]) > 0),
        key=lambda item: (-float(item["vector_score"]), item["case_id"]))[:RULES["per_route_candidate_limit"]]]
    keyword_ids = [item["case_id"] for item in sorted(
        (item for item in matches if float(item["keyword_score"]) > 0),
        key=lambda item: (-float(item["keyword_score"]), item["case_id"]))[:RULES["per_route_candidate_limit"]]]
    by_id = {item["case_id"]: item for item in matches}
    if strategy == "vector":
        scores = {key: float(by_id[key]["vector_score"]) for key in vector_ids}
    elif strategy == "legacy_score":
        scores = {key: legacy_hybrid_score(by_id[key]) for key in set(vector_ids) | set(keyword_ids)}
    elif vector_ids:
        vector_weight, keyword_weight = STRATEGIES[strategy]
        if kb_module.WRRF_RANK_CONSTANT != RULES["rank_constant"]:
            raise ValueError("Frozen rank constant differs from production")
        scores = _weighted_reciprocal_rank_scores(vector_ids, keyword_ids,
            vector_weight=vector_weight, keyword_weight=keyword_weight)
    else:
        scores = {key: float(by_id[key]["keyword_score"]) for key in keyword_ids}
    return [{**by_id[key], "score": scores[key]} for key in sorted(scores, key=lambda key: (-scores[key], key))]


def paired_comparison(rows, candidate="wrrf_093", baseline="wrrf_070"):
    differences = {metric: [row["strategies"][candidate]["metrics"][metric]
                            - row["strategies"][baseline]["metrics"][metric] for row in rows]
                   for metric in RULES["primary_metrics"]}
    generator = random.Random(RULES["bootstrap_seed"])
    samples = {metric: [] for metric in differences}
    count = len(rows)
    for _ in range(RULES["bootstrap_samples"]):
        indices = [generator.randrange(count) for _ in range(count)]
        for metric, values in differences.items():
            samples[metric].append(sum(values[i] for i in indices) / count)
    result = {}
    for metric, values in differences.items():
        ordered = sorted(samples[metric])
        result[metric] = {"mean_delta": sum(values) / count,
            "paired_bootstrap_95_ci": [ordered[int(len(ordered) * .025)], ordered[int(len(ordered) * .975) - 1]],
            "wins": sum(value > 1e-12 for value in values), "losses": sum(value < -1e-12 for value in values),
            "ties": sum(abs(value) <= 1e-12 for value in values)}
    result["observed_non_regression"] = (result["recall@5"]["mean_delta"] >= -1e-12
        and result["mrr@10"]["mean_delta"] >= -.01 - 1e-12)
    result["superiority_supported"] = (result["recall@5"]["paired_bootstrap_95_ci"][0] > 0
        and result["mrr@10"]["mean_delta"] >= -.01)
    return result


def states_for(histories):
    return [{"task_id": doc["experience_id"], "review_decision": "accepted", "original_report": doc["original_report"],
             "diagnosis": doc["diagnosis"], "patch_diff": doc["patch_diff"], "patch_hash": digest(doc["patch_diff"]),
             "source": {"project": "synthetic-holdout-only", "provenance": doc["provenance"],
                        "review": "synthetic_adapter_not_human_review"},
             "reproduction": {}, "applicable_conditions": doc["applicable_conditions"],
             "validation": {"passed": True, "summary": "synthetic retrieval adapter; not a newly executed repair"}}
            for doc in histories]


def run(protocol_path=PROTOCOL, output=RESULT):
    from code_repair.llm_client import embed_texts
    from code_repair.resilience import execution_scope
    protocol = load(protocol_path)
    if protocol["identity"] != identity():
        raise ValueError("Frozen inputs, rules, models or evaluator source changed; refusing measurement")
    if output.exists():
        raise FileExistsError("Refusing to overwrite a measured holdout report")
    states = states_for(corpus())
    data = load(DATA)["queries"]
    texts = [_case_search_text(json.loads(build_case_document(state))) for state in states]
    inputs = texts + [row["query"] for row in data]
    reranker = CrossEncoderReranker.from_environment()
    work = ROOT.parent / "work" / "wrrf-holdout-20261118"
    work.mkdir(parents=True, exist_ok=True)
    with execution_scope(1200, max_tokens=100000, trace_path=work / "trace.jsonl") as context:
        vectors = embed_texts(inputs, batch_size=8)
        if len(vectors) != len(inputs) or len({len(vector) for vector in vectors}) != 1:
            raise ValueError("Incomplete/mixed-dimension embedding batch")
        embedding_lookup = dict(zip(inputs, vectors))
        write_once(work / "vectors.json", {"protocol_sha256": digest(protocol), "input_sha256": digest(inputs),
                                          "vectors": vectors})
        reranker.warmup()
        results = []
        pre_rows, post_rows = [], []
        with tempfile.TemporaryDirectory(prefix="wrrf-holdout-") as temp:
            knowledge = KnowledgeBase(Path(temp) / "knowledge.sqlite3", embedder=embedding_lookup.__getitem__,
                                      qdrant_index=None, reranker=None)
            for state in states:
                knowledge.save_case(state)
            if knowledge.count() != len(states):
                raise ValueError("Unexpected corpus deduplication changed labeled population")
            id_to_vector = {state["task_id"]: vector for state, vector in zip(states, vectors)}
            for row in data:
                context.check()
                # Query once for the complete eligible score pool; weights never re-embed.
                matches = knowledge.search(row["query"], limit=len(states), min_similarity=RULES["min_similarity"],
                                           strategy="hybrid", rerank=False)
                if not matches or any(item["retrieval_mode"] != "hybrid" for item in matches):
                    raise ValueError("Embedding fallback cannot count as a hybrid holdout measurement")
                ranked = {name: collapse_semantic_duplicates(rank_candidates(matches, name), id_to_vector)
                          for name in STRATEGIES}
                # Assert reconstructed candidate limits/ties match the real default search path.
                actual = knowledge.search(row["query"], limit=RULES["return_limit"],
                    min_similarity=RULES["min_similarity"], strategy="hybrid", rerank=False)
                if [item["case_id"] for item in actual] != [item["case_id"] for item in ranked["wrrf_093"][:5]]:
                    raise ValueError("Evaluator disagrees with the production fusion path")
                union_ids = sorted({item["case_id"] for ranking in ranked.values() for item in ranking[:10]})
                by_id = {item["case_id"]: item for item in matches}
                union_texts = [_case_search_text(json.loads(by_id[key]["document"])) for key in union_ids]
                # Same text pair gets exactly the same real Cross-Encoder score in all strategies.
                union_scores = reranker.score(row["query"], union_texts)
                if len(union_scores) != len(union_ids) or not all(math.isfinite(score) for score in union_scores):
                    raise ValueError("Invalid reranking result; no silent fallback allowed")
                score_map = dict(zip(union_ids, union_scores))
                pre, post = {}, {}
                for name, ranking in ranked.items():
                    before = [item["case_id"] for item in ranking[:10]]
                    after = [item["case_id"] for item in sorted(ranking[:10],
                        key=lambda item: (score_map[item["case_id"]], item["score"]), reverse=True)]
                    pre[name] = {"ranked": before, "metrics": ranking_metrics(before, row["relevant"])}
                    post[name] = {"ranked": after, "metrics": ranking_metrics(after, row["relevant"])}
                pre_rows.append({"case_id": row["case_id"], "strategies": pre})
                post_rows.append({"case_id": row["case_id"], "strategies": post})
                results.append({**row, "vector_scores": {item["case_id"]: item["vector_score"] for item in matches},
                                "keyword_scores": {item["case_id"]: item["keyword_score"] for item in matches},
                                "cross_encoder_scores": score_map, "before_rerank": pre, "after_rerank": post})
        summary = {}
        for stage, rows in (("before_rerank", pre_rows), ("after_rerank", post_rows)):
            summary[stage] = {"strategies": {name: aggregate_ranking_metrics(
                [row["strategies"][name]["metrics"] for row in rows]) for name in STRATEGIES},
                "paired_selected_vs_original": paired_comparison(rows)}
        report = {"status": "measured", "measured_at": datetime.now(timezone.utc).isoformat(),
                  "protocol_sha256": digest(protocol), "frozen_identity": protocol["identity"],
                  "input_vector_sha256": digest(vectors), "embedding_dimension": len(vectors[0]),
                  "observed_tokens": context.tokens, "observed_cost_yuan": context.cost if context.cost_known else None,
                  "usage_complete": context.usage_known, "cost_complete": context.cost_known,
                  "active_reranker_provider": reranker.active_provider or reranker.provider,
                  "production_fusion_parity_checks": len(data), "summary": summary, "details": results,
                  "conclusion": {"observed_non_regression": all(summary[stage]["paired_selected_vs_original"]
                    ["observed_non_regression"] for stage in summary),
                    "production_generalization_verified": False, "weights_changed": False,
                    "independent_human_labels": False}}
        write_once(output, report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    args = parser.parse_args()
    value = prepare() if args.action == "prepare" else run()
    print(json.dumps({key: value[key] for key in ("status", "summary", "conclusion") if key in value}, ensure_ascii=True))


if __name__ == "__main__":
    main()
