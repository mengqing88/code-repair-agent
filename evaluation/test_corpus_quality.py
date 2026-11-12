import copy
import itertools

import pytest

from evaluation.corpus_quality import evaluate_pairs, records_digest


def fixture():
    records = [{"case_id": key, "outcome": "accepted", "created_at": key,
                "document": {"retrieval_scope": {"repository_id": "repo", "file_paths": ["code.py"],
                                                  "dependency_versions": {}}}}
               for key in ["a", "b", "c"]]
    annotation = {"records_sha256": records_digest(records), "producer": "author",
                  "permission_reference": "approved test fixture", "sampling_description": "synthetic edge cases",
                  "corpus_kind": "synthetic", "pairs": [
                      {"left": a, "right": b, "duplicate": (a, b) == ("a", "b"),
                       "reviewer": "reviewer", "evidence": "fixture expectation"}
                      for a, b in itertools.combinations(["a", "b", "c"], 2)]}
    return [{**row, "vector": [1.0, 0.0]} for row in records], annotation


def test_reports_false_merges_with_explicit_denominators():
    records, labels = fixture()
    report = evaluate_pairs(records, labels)
    assert report["confusion"] == {"tp": 1, "fp": 2, "tn": 0, "fn": 0}
    assert report["false_merge_fraction_among_merged_pairs"] == pytest.approx(2 / 3)
    assert report["false_positive_rate_among_distinct_pairs"] == 1
    assert not report["production_acceptance"]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "self_review", "wrong_snapshot"])
def test_refuses_incomplete_or_unbound_labels(damage):
    records, labels = fixture()
    if damage == "missing":
        labels["pairs"].pop()
    elif damage == "duplicate":
        labels["pairs"].append(copy.deepcopy(labels["pairs"][0]))
    elif damage == "self_review":
        labels["pairs"][0]["reviewer"] = "author"
    else:
        labels["records_sha256"] = "old"
    with pytest.raises(ValueError):
        evaluate_pairs(records, labels)


def test_no_merged_pair_is_unknown_fraction_instead_of_zero():
    records, labels = fixture()
    for i, row in enumerate(records):
        row["vector"] = [float(i == j) for j in range(3)]
    report = evaluate_pairs(records, labels)
    assert report["false_merge_fraction_among_merged_pairs"] is None
    assert report["missed_duplicate_rate"] == 1


def test_contradictory_equivalence_labels_are_rejected():
    records, labels = fixture()
    labels["pairs"][2]["duplicate"] = True
    with pytest.raises(ValueError, match="contradict"):
        evaluate_pairs(records, labels)
