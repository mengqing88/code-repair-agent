"""Score semantic merges against a complete, hash-bound independent pair annotation."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path

from knowledge_base import _case_search_text
from semantic_dedup import merge_similar_cases, scope_key


def records_digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def evaluate_pairs(records, annotation, threshold=0.97):
    if not records or len({row["case_id"] for row in records}) != len(records):
        raise ValueError("Corpus must contain unique nonempty case IDs")
    bound_records = [{key: value for key, value in row.items() if key != "vector"} for row in records]
    if annotation.get("records_sha256") != records_digest(bound_records):
        raise ValueError("Pair labels refer to a different corpus snapshot")
    if not annotation.get("permission_reference") or not annotation.get("sampling_description"):
        raise ValueError("Permission and sampling evidence are required")
    if any(scope_key(row["document"]) is None for row in records):
        raise ValueError("Each document needs complete production merge scope")
    ids = {row["case_id"] for row in records}
    all_pairs = {tuple(sorted(pair)) for pair in itertools.combinations(sorted(ids), 2)}
    labels = {}
    for pair in annotation.get("pairs", []):
        key = tuple(sorted([pair["left"], pair["right"]]))
        if key not in all_pairs or key in labels or type(pair.get("duplicate")) is not bool:
            raise ValueError("Invalid or duplicate pair annotation")
        reviewer = pair.get("reviewer")
        if not reviewer or reviewer == annotation.get("producer") or not pair.get("evidence"):
            raise ValueError("Independent reviewer and per-pair evidence are required")
        labels[key] = pair["duplicate"]
    if set(labels) != all_pairs:
        raise ValueError("Annotate every pair; missing labels must not silently become negative")
    truth_parent = {key: key for key in ids}
    def root(key):
        while truth_parent[key] != key:
            key = truth_parent[key]
        return key
    for (left, right), duplicate in labels.items():
        if duplicate:
            truth_parent[root(left)] = root(right)
    if any(not duplicate and root(left) == root(right) for (left, right), duplicate in labels.items()):
        raise ValueError("Duplicate equivalence labels contradict each other")
    links = merge_similar_cases(records, threshold=threshold)
    canonical = {key: key for key in ids}
    for link in links:
        canonical[link["duplicate_case_id"]] = link["canonical_case_id"]
    counts = Counter(tp=0, fp=0, tn=0, fn=0)
    mistakes = []
    for (left, right), duplicate in labels.items():
        merged = canonical[left] == canonical[right]
        key = ("tp" if duplicate else "fp") if merged else ("fn" if duplicate else "tn")
        counts[key] += 1
        if key in {"fp", "fn"}:
            mistakes.append({"left": left, "right": right, "classification": key})
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    return {"records_sha256": annotation["records_sha256"], "threshold": threshold,
            "pairs": len(labels), "confusion": dict(counts), "links": links, "mistakes": mistakes,
            "false_merge_fraction_among_merged_pairs": ratio(counts["fp"], counts["tp"] + counts["fp"]),
            "false_positive_rate_among_distinct_pairs": ratio(counts["fp"], counts["fp"] + counts["tn"]),
            "missed_duplicate_rate": ratio(counts["fn"], counts["tp"] + counts["fn"]),
            "corpus_kind_claim": annotation.get("corpus_kind"), "production_acceptance": False,
            "identity_boundary": "Review JSON is not identity verification; operator must verify evidence and representative sampling",
            "sample_limit": "Empirical finite-corpus rates, no automatic population or production-SLO claim"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path")
    from sentence_transformers import SentenceTransformer
    import torch
    torch.set_num_threads(2)
    records = json.loads(args.records.read_text(encoding="utf-8"))
    labels = json.loads(args.annotations.read_text(encoding="utf-8"))
    model = SentenceTransformer(str(args.embedding_model.resolve()), device="cpu", local_files_only=True)
    vectors = model.encode([_case_search_text(row["document"]) for row in records],
                           normalize_embeddings=True, show_progress_bar=False)
    report = evaluate_pairs([{**row, "vector": vector.tolist()} for row, vector in zip(records, vectors)], labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "links"}, indent=2))


if __name__ == "__main__":
    main()
